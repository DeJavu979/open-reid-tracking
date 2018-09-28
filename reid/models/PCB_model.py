from __future__ import absolute_import

import torch
from torch import nn
from torch.nn import init
from torch.autograd import Variable
from torch.nn import functional as F
from .resnet import *
import torchvision


class PCB_model(nn.Module):
    def __init__(self, num_stripes=6, num_features=256, num_classes=0, norm=False, dropout=0, last_stride=1,
                 output_feature='fc'):
        super(PCB_model, self).__init__()
        # Create PCB_only model
        self.num_stripes = num_stripes
        self.num_features = num_features
        self.num_classes = num_classes
        self.rpp = False
        self.output_feature = output_feature

        # ResNet50: from 3*384*128 -> 2048*24*8 (Tensor T; of column vector f's)
        self.base = nn.Sequential(
            *list(resnet50(pretrained=True, cut_at_pooling=True, norm=norm, dropout=dropout).base.children())[:-2])
        # decrease the downsampling rate
        if last_stride != 2:
            # decrease the downsampling rate
            self.base[7][0].conv2.stride = last_stride
            self.base[7][0].downsample[0].stride = last_stride

        '''Average Pooling: 256*24*8 -> 256*6*1 (f -> g)'''
        # Tensor T [N, 256, 24, 8]
        self.avg_pool = nn.AdaptiveAvgPool2d((6, 1))

        # dropout after pool5 (or what left of it) at p=0.5
        self.dropout = dropout
        if self.dropout > 0:
            self.drop_layer = nn.Dropout2d(self.dropout)
        out_planes = 2048
        self.local_conv = nn.Conv2d(out_planes, self.num_features, kernel_size=1, padding=0, bias=False)
        init.kaiming_normal_(self.local_conv.weight, mode='fan_out')
        self.feat_bn2d = nn.BatchNorm2d(self.num_features)
        init.constant_(self.feat_bn2d.weight, 1)
        init.constant_(self.feat_bn2d.bias, 0)

        # 6 branches of fc's:
        if self.num_classes > 0:
            self.fc_s = nn.ModuleList()
            for _ in range(self.num_stripes):
                fc = nn.Linear(self.num_features, self.num_classes)
                init.normal_(fc.weight, std=0.001)
                init.constant_(fc.bias, 0)
                self.fc_s.append(fc)

    def forward(self, x):
        """
        Returns:
          h_s: each member with shape [N, c]
          prediction_s: each member with shape [N, num_classes]
        """
        # Tensor T [N, 2048, 24, 8]
        x = self.base(x)
        f_shape = x.size()

        # g_s [N, 2048, 6, 1]
        x = self.avg_pool(x)

        out0 = x / x.norm(2, 1).unsqueeze(1).expand_as(x)
        if self.dropout:
            x = self.drop_layer(x)
        x = self.local_conv(x)
        out1 = x / x.norm(2, 1).unsqueeze(1).expand_as(x)
        x = self.feat_bn2d(x)
        x = F.relu(x)  # relu for local_conv feature

        x_s = x.chunk(self.num_stripes, 2)
        prediction_s = []
        for i in range(self.num_stripes):
            # 4d vector h -> 2d vector h
            x = x_s[i].view(f_shape[0], -1)
            prediction_s.append(self.fc_s[i](x))
        if self.output_feature == 'pool5':
            return out0, tuple(prediction_s)
        else:
            return out1, tuple(prediction_s)
