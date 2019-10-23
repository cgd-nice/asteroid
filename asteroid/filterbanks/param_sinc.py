"""
Sinc-based parameterized filterbank.
@author : Manuel Pariente, Inria-Nancy
Inspired from https://github.com/mravanelli/SincNet
"""

import numpy as np
import torch
import torch.nn as nn
from .enc_dec import EncoderDecoder


class SincParamFB(EncoderDecoder):
    """ Extension of the parameterized filterbank from [1] proposed in [2].
    # Arguments
        n_filters: Positive int. Number of filters.
        kernel_size: Positive int. Length of the filters.
        stride: Positive int. Stride of the convolution.
        enc_or_dec: String. `enc` or `dec`. Controls if filterbank is used as
            an encoder of a decoder.
        min_low_hz: Positive int. Lowest low frequency allowed (Hz).
        min_band_hz: Positive int. Lowest band frequency allowed (Hz).
    # References
        [1] : "Speaker Recognition from raw waveform with SincNet". SLT 2018.
        Mirco Ravanelli, Yoshua Bengio.  https://arxiv.org/abs/1808.00158
        [2] : "Filterbank design for end-to-end speech separation".
        Submitted to ICASSP 2020. Manuel Pariente, Samuele Cornell,
        Antoine Deleforge, Emmanuel Vincent.
    """
    def __init__(self, n_filters, kernel_size, stride, enc_or_dec='encoder',
                 sample_rate=16000, min_low_hz=50, min_band_hz=50):
        super(SincParamFB, self).__init__(stride, enc_or_dec=enc_or_dec)
        self.n_filters = n_filters
        if kernel_size % 2 == 0:
            print('Received kernel_size={}, force '.format(kernel_size) +
                  'kernel_size={} so filters are odd'.format(kernel_size+1))
            kernel_size += 1
        self.kernel_size = kernel_size
        self.stride = stride
        self.sample_rate = sample_rate
        self.min_low_hz, self.min_band_hz = min_low_hz, min_band_hz
        self.cutoff = self.kernel_size // 2
        self._initialize_filters()
        window_ = np.hamming(self.kernel_size)[:self.cutoff]  # Half window
        n_ = 2 * np.pi * (torch.arange(-self.cutoff, 0.).view(1, -1) /
                          self.sample_rate)  # Half time vector
        self.register_buffer('window_', torch.Tensor(window_))
        self.register_buffer('n_', n_)

    def _initialize_filters(self):
        """ Filter Initialization along the Mel scale"""
        low_hz = 30
        high_hz = self.sample_rate / 2 - (self.min_low_hz + self.min_band_hz)
        mel = np.linspace(self.to_mel(low_hz),
                          self.to_mel(high_hz),
                          self.n_filters // 2 + 1)
        hz = self.to_hz(mel)
        # filters parameters (out_channels // 2, 1)
        self.low_hz_ = nn.Parameter(torch.Tensor(hz[:-1]).view(-1, 1))
        self.band_hz_ = nn.Parameter(torch.Tensor(np.diff(hz)).view(-1, 1))

    @property
    def filters(self):
        """ Compute filters from parameters """
        low = self.min_low_hz + torch.abs(self.low_hz_)
        high = torch.clamp(low + self.min_band_hz + torch.abs(self.band_hz_),
                           self.min_low_hz, self.sample_rate / 2)
        cos_filters = self.make_filters(low, high, type='cos')
        sin_filters = self.make_filters(low, high, type='sin')
        return torch.cat([cos_filters, sin_filters], dim=0)

    def make_filters(self, low, high, type='cos'):
        band = (high - low)[:, 0]
        f_times_t_low = torch.matmul(low, self.n_)
        f_times_t_high = torch.matmul(high, self.n_)
        if type == 'cos':  # Even filters from the SincNet paper.
            bp_left = ((torch.sin(f_times_t_high) -
                               torch.sin(f_times_t_low)) / (
                                          self.n_ / 2)) * self.window_
            bp_center = 2 * band.view(-1, 1)
            bp_right = torch.flip(bp_left, dims=[1])
        elif type == 'sin':  # Extension including odd filters
            bp_left = ((torch.cos(f_times_t_low) -
                               torch.cos(f_times_t_high)) / (
                                          self.n_ / 2)) * self.window_
            bp_center = torch.zeros_like(band.view(-1, 1))
            bp_right = - torch.flip(bp_left, dims=[1])
        else:
            raise ValueError('Invalid filter type {}'.format(type))
        band_pass = torch.cat([bp_left, bp_center, bp_right], dim=1)
        band_pass = band_pass / (2 * band[:, None])
        return band_pass.view(self.n_filters // 2, 1, self.kernel_size)

    @staticmethod
    def to_mel(hz):
        return 2595 * np.log10(1 + hz / 700)

    @staticmethod
    def to_hz(mel):
        return 700 * (10 ** (mel / 2595) - 1)