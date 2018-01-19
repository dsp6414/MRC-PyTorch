#!/usr/bin/env python
# -*- coding: utf-8 -*-

__author__ = 'han'

import h5py
import torch
from torch.autograd import Variable
import torch.nn.functional as F
import numpy as np


class GloveEmbedding(torch.nn.Module):
    """
    Glove Embedding Layer
    Args:
        - glove_h5_path: glove embedding file path
    Inputs:
        **input** sequence with word index
    Outputs
        **output** tensor that change word index to word embeddings
    """

    def __init__(self, glove_h5_path):
        super(GloveEmbedding, self).__init__()
        self.glove_h5_path = glove_h5_path
        self.n_embeddings, self.len_embedding, self.weights = self.load_glove_hdf5()

        self.embedding_layer = torch.nn.Embedding(num_embeddings=self.n_embeddings, embedding_dim=self.len_embedding)
        self.embedding_layer.weight = torch.nn.Parameter(self.weights)
        self.embedding_layer.weight.requires_grad = False

    def load_glove_hdf5(self):
        with h5py.File(self.glove_h5_path, 'r') as f:
            id2vec = np.array(f['id2vec'])
            word_dict_size = f.attrs['word_dict_size']
            embedding_size = f.attrs['embedding_size']

        return int(embedding_size), int(word_dict_size), torch.from_numpy(id2vec)

    def forward(self, x):
        return self.embedding_layer.forward(x)  # todo: 去掉padding加的冗余后缀


class MatchLSTMAttention(torch.nn.Module):
    r"""
    attention mechanism in match-lstm
    Args:
        - input_size: The number of expected features in the input Hp and Hq
        - hidden_size: The number of features in the hidden state Hr

    Inputs:
        Hp(1, batch, input_size): a context word encoded
        Hq(question_len, batch, input_size): whole question encoded
        Hr_last(batch, hidden_size): last lstm hidden output

    Outputs:
        alpha(batch, question_len): attention vector
    """

    def __init__(self, input_size, hidden_size):
        super(MatchLSTMAttention, self).__init__()

        self.linear_wq = torch.nn.Linear(input_size, hidden_size)
        self.linear_wp = torch.nn.Linear(input_size, hidden_size)
        self.linear_wr = torch.nn.Linear(hidden_size, hidden_size)
        self.linear_wg = torch.nn.Linear(hidden_size, 1)

    def forward(self, Hpi, Hq, Hr_last):
        wq_hq = self.linear_wq(Hq)                      # (question_len, batch, hidden_size)
        wp_hp = self.linear_wp(Hpi).unsqueeze(0)        # (1, batch, hidden_size)
        wr_hr = self.linear_wr(Hr_last).unsqueeze(0)    # (1, batch, hidden_size)
        G = F.tanh(wq_hq + wp_hp + wr_hr)               # (question_len, batch, hidden_size)
        wg_g = self.linear_wg(G).squeeze(2)             # (question_len, batch)
        alpha = F.softmax(wg_g, dim=1).transpose(0, 1)  # (batch, question_len)    todo: verify dim

        return alpha


class UniMatchLSTM(torch.nn.Module):
    r"""
    interaction context and question with attention mechanism, one direction
    Args:
        - input_size: The number of expected features in the input Hp and Hq
        - hidden_size: The number of features in the hidden state Hr

    Inputs:
        Hp(context_len, batch, input_size): context encoded
        Hq(question_len, batch, input_size): question encoded

    Outputs:
        Hr(context_len, batch, hidden_size): question-aware context representation
    """

    def __init__(self, input_size, hidden_size):
        super(UniMatchLSTM,self).__init__()
        self.input_size = input_size
        self.hidden_size = hidden_size

        self.attention = MatchLSTMAttention(input_size, hidden_size)
        self.lstm = torch.nn.LSTMCell(input_size=2*input_size, hidden_size=hidden_size)

    def init_hidden(self, batch_size):
        return (Variable(torch.zeros(1, batch_size, self.hidden_size)),
                Variable(torch.zeros(1, batch_size, self.hidden_size)))

    def forward(self, Hp, Hq):
        batch_size = Hp.size[1]
        context_len = Hp.size[0]
        hidden_out = [self.init_hidden(batch_size)]

        for t in range(context_len):
            cur_hp = Hp[t, ...].squeeze(0)                              # (batch, input_size)
            alpha = self.attention.forward(cur_hp, Hq, hidden_out)      # (batch, question_len)
            question_alpha = torch.bmm(alpha.unsqueeze(1), Hq.transpose(0, 1))\
                .transpose(0, 1)\
                .squeeze(0)                                             # (batch, input_size)
            cur_z = torch.cat([cur_hp, question_alpha], dim=1)          # (batch, 2*input_size)

            cur_hidden, _ = self.lstm.forward(cur_z, hidden_out[t])     # (batch, hidden_size), (batch, hidden_size)
            hidden_out.append(cur_hidden)

        return hidden_out[1:]


class MatchLSTM(torch.nn.Module):
    r"""
    interaction context and question with attention mechanism
    Args:
        - input_size: The number of expected features in the input Hp and Hq
        - hidden_size: The number of features in the hidden state Hr
        - bidirectional: If ``True``, becomes a bidirectional RNN. Default: ``False``

    Inputs:
        Hp(context_len, batch, input_size): context encoded
        Hq(question_len, batch, input_size): question encoded
    Outputs:
        Hr(context_len, batch, hidden_size * num_directions): question-aware context representation
    """
    def __init__(self, input_size, hidden_size, bidirectional):
        super(MatchLSTM, self).__init__()
        self.bidirectional = bidirectional
        self.num_directions = 1 if bidirectional else 2

        self.left_match_lstm = UniMatchLSTM(input_size, hidden_size)

        if bidirectional:
            self.right_match_lstm = UniMatchLSTM(input_size, hidden_size)

    def flip(self, tensor, dim=0):
        idx = [i for i in range(tensor.size(dim) - 1, -1, -1)]
        idx = torch.autograd.Variable(torch.LongTensor(idx))
        inverted_tensor = tensor.index_select(dim, idx)
        return inverted_tensor

    def forward(self, Hp, Hq):
        left_hidden = self.left_match_lstm.forward(Hp, Hq)
        rtn_hidden = left_hidden

        if self.bidirectional:
            Hp_inv = self.flip(Hp, dim=0)
            right_hidden = self.right_match_lstm.forward(Hp_inv, Hq)
            rtn_hidden = torch.cat([left_hidden, right_hidden], dim=2)

        return rtn_hidden


class SeqPointer(torch.nn.Module):
    r"""
    Sequence Pointer Net that output every possible answer position in context
    Args:

    Inputs:
        Hr: question-aware context representation
    Outputs:
        **output** every answer index possibility position in context, no fixed length
    """

    def __init__(self):
        super(SeqPointer, self).__init__()

    def forward(self, *input):
        pass


class BoundaryPointer(torch.nn.Module):
    r"""
    boundary Pointer Net that output start and end possible answer position in context
    Args:

    Inputs:
        Hr: question-aware context representation
    Outputs:
        **output** start and end answer index possibility position in context, fixed length
    """

    def __init__(self):
        super(BoundaryPointer, self).__init__()

    def forward(self, *input):
        pass