# -*- coding: utf-8 -*-
import os
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import torch
import torch.backends.cudnn as cudnn
from PyQt5.QtCore import pyqtSignal, QThread
from PyQt5.QtGui import QImage, QPixmap, QPainter
from PyQt5.QtWidgets import QApplication, QMainWindow, QFileDialog

from main_ui_light import Ui_MainWindow

FILE = Path(__file__).absolute()
sys.path.append(FILE.parents[0].as_posix())  # add yolov5/ to path

from models.common import DetectMultiBackend
from utils.datasets import LoadImages, LoadWebcam
from utils.general import (check_img_size, check_imshow, non_max_suppression, scale_coords)
from utils.plots import Annotator, colors
from utils.torch_utils import select_device, time_sync


class DetThread(QThread):
    send_img = pyqtSignal(np.ndarray)
    send_raw = pyqtSignal(np.ndarray)
    send_statistic = pyqtSignal(dict)

    def __init__(self):
        super(DetThread, self).__init__()
        self.weights = './yolov5s.pt'
        self.source = '0'
        self.conf_thres = 0.25

    @torch.no_grad()
    def run(self,
            imgsz=640,  # inference size (pixels)
            iou_thres=0.45,  # NMS IOU threshold
            max_det=1000,  # maximum detections per image
            device='',  # cuda device, i.e. 0 or 0,1,2,3 or cpu
            view_img=True,  # show results
            save_txt=False,  # save results to *.txt
            save_conf=False,  # save confidences in --save-txt labels
            save_crop=False,  # save cropped prediction boxes
            nosave=False,  # do not save images/videos
            classes=None,  # filter by class: --class 0, or --class 0 2 3
            agnostic_nms=False,  # class-agnostic NMS
            augment=False,  # augmented inference
            visualize=False,  # visualize features
            update=False,  # update all models
            project='runs/detect',  # save results to project/name
            name='exp',  # save results to project/name
            exist_ok=False,  # existing project/name ok, do not increment
            line_thickness=3,  # bounding box thickness (pixels)
            hide_labels=False,  # hide labels
            hide_conf=False,  # hide confidences
            half=False,  # use FP16 half-precision inference
            dnn=False,  # use OpenCV DNN for ONNX inference
            ):

        # Initialize
        device = select_device(device)
        half &= device.type != 'cpu'  # half precision only supported on CUDA

        # Load model
        model = DetectMultiBackend(self.weights, device=device, dnn=dnn)
        num_params = 0
        for param in model.parameters():
            num_params += param.numel()
        stride, names, pt, jit, onnx, engine = model.stride, model.names, model.pt, model.jit, model.onnx, model.engine
        imgsz = check_img_size(imgsz, s=stride)  # check image size
        names = model.module.names if hasattr(model, 'module') else model.names  # get class names
        if half:
            model.half()  # to FP16

        # Dataloader
        if self.source.isnumeric():
            view_img = check_imshow()
            cudnn.benchmark = True  # set True to speed up constant image size inference
            dataset = LoadWebcam(self.source, img_size=imgsz, stride=stride)
            bs = len(dataset)  # batch_size
        else:
            dataset = LoadImages(self.source, img_size=imgsz, stride=stride, auto=pt and not jit)
            bs = 1  # batch_size
        vid_path, vid_writer = [None] * bs, [None] * bs

        # Run inference
        # model.warmup(imgsz=(1, 3, *imgsz), half=half)  # warmup
        dt, seen = [0.0, 0.0, 0.0], 0
        for path, im, im0s, self.vid_cap, s in dataset:
            statistic_dic = {name: 0 for name in names}
            t1 = time_sync()
            im = torch.from_numpy(im).to(device)
            im = im.half() if half else im.float()  # uint8 to fp16/32
            im /= 255  # 0 - 255 to 0.0 - 1.0
            if len(im.shape) == 3:
                im = im[None]  # expand for batch dim
            t2 = time_sync()
            dt[0] += t2 - t1

            # Inference
            pred = model(im, augment=augment)
            t3 = time_sync()
            dt[1] += t3 - t2

            # NMS
            pred = non_max_suppression(pred, self.conf_thres, iou_thres, classes, agnostic_nms, max_det=max_det)
            dt[2] += time_sync() - t3

            for i, det in enumerate(pred):  # detections per image
                im0 = im0s.copy()
                annotator = Annotator(im0, line_width=line_thickness, example=str(names))
                if len(det):
                    det[:, :4] = scale_coords(im.shape[2:], det[:, :4], im0.shape).round()
                    for c in det[:, -1].unique():
                        n = (det[:, -1] == c).sum()  # detections per class
                        s += f"{n} {names[int(c)]}{'s' * (n > 1)}, "  # add to string

                    for *xyxy, conf, cls in reversed(det):
                        c = int(cls)  # integer class
                        statistic_dic[names[c]] += 1
                        label = None if hide_labels else (names[c] if hide_conf else f'{names[c]} {conf:.2f}')
                        annotator.box_label(xyxy, label, color=colors(c, True))

            time.sleep(1 / 40)
            # print(type(im0s))
            self.send_img.emit(im0)
            self.send_raw.emit(im0s if isinstance(im0s, np.ndarray) else im0s[0])
            self.send_statistic.emit(statistic_dic)


class MainWindow(QMainWindow, Ui_MainWindow):
    def __init__(self, parent=None):
        super(MainWindow, self).__init__(parent)
        self.setupUi(self)
        self.model = './yolov5s.pt'
        self.det_thread = DetThread()
        self.det_thread.source = '0'
        self.det_thread.send_img.connect(lambda x: self.show_image(x, self.label_result))
        self.det_thread.send_raw.connect(lambda x: self.show_image(x, self.label_raw))
        self.det_thread.send_statistic.connect(self.show_statistic)
        # self.RunProgram.triggered.connect(lambda: self.det_thread.start())
        self.RunProgram.triggered.connect(self.term_or_con)
        self.SelFile.triggered.connect(self.open_file)
        self.SelModel.triggered.connect(self.open_model)
        self.status_bar_init()
        self.cam_switch.triggered.connect(self.camera)
        self.horizontalSlider.valueChanged.connect(lambda: self.conf_change(self.horizontalSlider))

    def paintEvent(self, event):
        painter = QPainter(self)
        pixmap = QPixmap()
        pixmap = QPixmap("./imgs/background.jpg")
        painter.drawPixmap(self.rect(), pixmap)

    # 更改置信度
    def conf_change(self, method):
        self.det_thread.conf_thres = self.horizontalSlider.value() / 100
        self.statusbar.showMessage("The confidence threshold is modified to：" + str(self.det_thread.conf_thres))

    def status_bar_init(self):
        self.statusbar.showMessage('')

    def open_file(self):
        source = QFileDialog.getOpenFileName(self, 'Select a video or picture', os.getcwd(),
                                             "Pic File(*.mp4 *.mkv *.avi *.flv "
                                             "*.jpg *.png)")
        if source[0]:
            self.det_thread.source = source[0]
        self.statusbar.showMessage('Loading files：{}'.format(os.path.basename(self.det_thread.source)
                                                             if os.path.basename(self.det_thread.source) != '0'
                                                             else 'Webcam'))

    def term_or_con(self):
        if self.RunProgram.isChecked():
            self.det_thread.start()
            self.statusbar.showMessage('Now detecting >> Model：{}，File：{}'.
                                       format(os.path.basename(self.det_thread.weights),
                                              os.path.basename(self.det_thread.source)
                                              if os.path.basename(self.det_thread.source) != '0'
                                              else 'Webcam'))
        else:
            self.det_thread.terminate()
            if hasattr(self.det_thread, 'vid_cap'):
                if self.det_thread.vid_cap:
                    self.det_thread.vid_cap.release()
            self.statusbar.showMessage('Detecting stopped')

    def open_model(self):
        self.model = QFileDialog.getOpenFileName(self, 'Select model', os.getcwd(), "Model File(*.pt)")[0]
        if self.model:
            self.det_thread.weights = self.model
        self.statusbar.showMessage('Loading models：' + os.path.basename(self.det_thread.weights))

    def camera(self):
        if self.cam_switch.isChecked():
            self.det_thread.source = '0'
            self.statusbar.showMessage('Camera is turned on')
        else:
            self.det_thread.terminate()
            if hasattr(self.det_thread, 'vid_cap'):
                self.det_thread.vid_cap.release()
            if self.RunProgram.isChecked():
                self.RunProgram.setChecked(False)
            self.statusbar.showMessage('Camera turned off')

    def show_statistic(self, statistic_dic):
        try:
            self.listWidget.clear()
            statistic_dic = sorted(statistic_dic.items(), key=lambda x: x[1], reverse=True)
            statistic_dic = [i for i in statistic_dic if i[1] > 0]
            results = [str(i[0]) + '：' + str(i[1]) for i in statistic_dic]
            self.listWidget.addItems(results)
            print(results)
            # self.statusbar.showMessage(results)
        except Exception as e:
            print(repr(e))

    @staticmethod
    def show_image(img_src, label):
        try:
            ih, iw, _ = img_src.shape
            w = label.geometry().width()
            h = label.geometry().height()
            if iw > ih:
                scal = w / iw
                nw = w
                nh = int(scal * ih)
                img_src_ = cv2.resize(img_src, (nw, nh))
            else:
                scal = h / ih
                nw = int(scal * iw)
                nh = h
                img_src_ = cv2.resize(img_src, (nw, nh))
            frame = cv2.cvtColor(img_src_, cv2.COLOR_BGR2RGB)
            img = QImage(frame.data, frame.shape[1], frame.shape[0], frame.shape[2] * frame.shape[1],
                         QImage.Format_RGB888)
            label.setPixmap(QPixmap.fromImage(img))
        except Exception as e:
            print(repr(e))


if __name__ == "__main__":
    app = QApplication(sys.argv)
    myWin = MainWindow()
    myWin.show()
    sys.exit(app.exec_())
