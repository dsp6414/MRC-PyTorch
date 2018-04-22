#!/usr/bin/env python
# -*- coding: utf-8 -*-

__author__ = 'han'

import os
import torch
import logging
import nltk
import numpy as np
import matplotlib.pyplot as plt
from dataset.squad_dataset import SquadDataset
from models.match_lstm import MatchLSTMModel
from utils.load_config import init_logging, read_config
from utils.functions import to_long_variable, count_parameters, draw_heatmap_sea, flip

init_logging()
logger = logging.getLogger(__name__)


def main():
    logger.info('------------Match-LSTM TEST INPUT--------------')
    logger.info('loading config file...')
    global_config = read_config()

    # set random seed
    seed = global_config['model']['global']['random_seed']
    enable_cuda = global_config['test']['enable_cuda']
    torch.manual_seed(seed)

    logger.info('reading squad dataset...')
    dataset = SquadDataset(global_config)

    logger.info('constructing model...')
    model = MatchLSTMModel(global_config)
    if enable_cuda:
        model = model.cuda()
    model.eval()  # let training = False, make sure right dropout

    logging.info('model parameters count: %d' % count_parameters(model))

    # load model weight
    logger.info('loading model weight...')
    model_weight_path = global_config['data']['model_path']
    is_exist_model_weight = os.path.exists(model_weight_path)
    assert is_exist_model_weight, "not found model weight file on '%s'" % model_weight_path

    weight = torch.load(model_weight_path, map_location=lambda storage, loc: storage)
    model.load_state_dict(weight, strict=False)

    # manual input qa
    context = "In 1870, Tesla moved to Karlovac, to attend school at the Higher Real Gymnasium, where he was " \
             "profoundly influenced by a math teacher Martin Sekuli\u0107.:32 The classes were held in German, " \
             "as it was a school within the Austro-Hungarian Military Frontier. Tesla was able to perform integral " \
             "calculus in his head, which prompted his teachers to believe that he was cheating. He finished a " \
             "four-year term in three years, graduating in 1873.:33 "
    question1 = "What language were classes held in at Tesla's school?"
    answer1 = ["German"]

    question2 = "Who was Tesla influenced by while in school?"
    answer2 = ["Martin Sekuli\u0107"]

    question3 = "Why did Tesla go to Karlovac?"
    answer3 = ["attend school at the Higher Real Gymnasium", 'to attend school']

    # change here to select questions
    question = question3
    answer = answer3[0]

    # preprocess
    context_token = nltk.word_tokenize(context)
    question_token = nltk.word_tokenize(question)

    a = np.array(context_token)

    context_id = dataset.sentence_word2id(context_token)
    question_id = dataset.sentence_word2id(question_token)

    context_id_char = dataset.sentence_char2id(context_token)
    question_id_char = dataset.sentence_char2id(question_token)

    context_var, question_var, context_var_char, question_var_char = [to_long_variable(x, volatile=True).unsqueeze(0)
                                                                      for x in [context_id, question_id,
                                                                                context_id_char, question_id_char]]

    out_ans_prop, out_ans_range, vis_param = model.forward(context_var, question_var, context_var_char, question_var_char)
    out_ans_range = out_ans_range.cpu().data.numpy()

    start = out_ans_range[0][0]
    end = out_ans_range[0][1] + 1

    out_answer_id = context_id[start:end]
    out_answer = dataset.sentence_id2word(out_answer_id)

    logging.info('Predict Answer: ' + ' '.join(out_answer))

    # to show on visdom
    s = 0
    e = 48

    x_left = vis_param['match']['left'][0, :, s:e].cpu().data.numpy()
    x_right = flip(vis_param['match']['right'], 2)[0, :, s:e].cpu().data.numpy()

    draw_heatmap_sea(x_left,
                     xlabels=context_token[s:e],
                     ylabels=question_token,
                     answer=answer,
                     save_path='data/test-left.png',
                     bottom=0.45)
    draw_heatmap_sea(x_right,
                     xlabels=context_token[s:e],
                     ylabels=question_token,
                     answer=answer,
                     save_path='data/test-right.png',
                     bottom=0.45)

    if global_config['model']['interaction']['self_match_lstm']:
        x_self_left = vis_param['self']['left'][0, s:e, s:e].cpu().data.numpy()
        x_self_right = flip(vis_param['self']['right'], 2)[0, s:e, s:e].cpu().data.numpy()

        draw_heatmap_sea(x_self_left,
                         xlabels=context_token[s:e],
                         ylabels=context_token[s:e],
                         answer=answer,
                         save_path='data/test-self-left.png',
                         inches=(11, 11),
                         bottom=0.2)
        draw_heatmap_sea(x_self_right,
                         xlabels=context_token[s:e],
                         ylabels=context_token[s:e],
                         answer=answer,
                         save_path='data/test-self-right.png',
                         inches=(11, 11),
                         bottom=0.2)
    # plt.show()


if __name__ == '__main__':
    main()