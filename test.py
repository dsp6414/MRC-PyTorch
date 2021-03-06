#!/usr/bin/env python
# -*- coding: utf-8 -*-

__author__ = 'han'

import json
import os
import torch
import logging
import argparse
from dataset.squad_dataset import SquadDataset
from models.match_lstm import MatchLSTMModel
from utils.load_config import init_logging, read_config
from models.loss import MyNLLLoss
from utils.eval import eval_on_model

init_logging()
logger = logging.getLogger(__name__)


def main(config_path, out_path):
    logger.info('------------Match-LSTM Evaluate--------------')
    logger.info('loading config file...')
    global_config = read_config(config_path)

    # set random seed
    seed = global_config['model']['global']['random_seed']
    torch.manual_seed(seed)

    enable_cuda = global_config['test']['enable_cuda']
    device = torch.device("cuda" if enable_cuda else "cpu")
    if torch.cuda.is_available() and not enable_cuda:
        logger.warning("CUDA is avaliable, you can enable CUDA in config file")
    elif not torch.cuda.is_available() and enable_cuda:
        raise ValueError("CUDA is not abaliable, please unable CUDA in config file")

    torch.no_grad()  # make sure all tensors below have require_grad=False

    logger.info('reading squad dataset...')
    dataset = SquadDataset(global_config)

    logger.info('constructing model...')
    model = MatchLSTMModel(global_config).to(device)
    model.eval()  # let training = False, make sure right dropout

    # load model weight
    logger.info('loading model weight...')
    model_weight_path = global_config['data']['model_path']
    assert os.path.exists(model_weight_path), "not found model weight file on '%s'" % model_weight_path

    weight = torch.load(model_weight_path, map_location=lambda storage, loc: storage)
    if enable_cuda:
        weight = torch.load(model_weight_path, map_location=lambda storage, loc: storage.cuda())
    model.load_state_dict(weight, strict=False)

    # forward
    logger.info('forwarding...')

    enable_char = global_config['model']['encoder']['enable_char']
    batch_size = global_config['test']['batch_size']
    # batch_dev_data = dataset.get_dataloader_dev(batch_size)
    batch_dev_data = list(dataset.get_batch_dev(batch_size))

    # to just evaluate score or write answer to file
    if out_path is None:
        criterion = MyNLLLoss()
        score_em, score_f1, sum_loss = eval_on_model(model=model,
                                                     criterion=criterion,
                                                     batch_data=batch_dev_data,
                                                     epoch=None,
                                                     device=device,
                                                     enable_char=enable_char,
                                                     batch_char_func=dataset.gen_batch_with_char)
        logger.info("test: ave_score_em=%.2f, ave_score_f1=%.2f, sum_loss=%.5f" % (score_em, score_f1, sum_loss))
    else:
        predict_ans = predict_on_model(model=model,
                                       batch_data=batch_dev_data,
                                       device=device,
                                       enable_char=enable_char,
                                       batch_char_func=dataset.gen_batch_with_char,
                                       id_to_word_func=dataset.sentence_id2word)
        samples_id = dataset.get_all_samples_id_dev()
        ans_with_id = dict(zip(samples_id, predict_ans))

        logging.info('writing predict answer to file %s' % out_path)
        with open(out_path, 'w') as f:
            json.dump(ans_with_id, f)

    logging.info('finished.')


def predict_on_model(model, batch_data, device, enable_char, batch_char_func, id_to_word_func):
    batch_cnt = len(batch_data)
    answer = []

    for bnum, batch in enumerate(batch_data):

        # batch data
        bat_context, bat_question, bat_context_char, bat_question_char, bat_answer_range = \
            batch_char_func(batch, enable_char=enable_char, device=device)

        _, tmp_ans_range, _ = model.forward(bat_context, bat_question, bat_context_char, bat_question_char)
        tmp_context_ans = zip(bat_context.cpu().data.numpy(),
                              tmp_ans_range.cpu().data.numpy())
        tmp_ans = [' '.join(id_to_word_func(c[a[0]:(a[1] + 1)])) for c, a in tmp_context_ans]
        answer += tmp_ans

        logging.info('batch=%d/%d' % (bnum, batch_cnt))

        # manual release memory, todo: really effect?
        del bat_context, bat_question, bat_answer_range, bat_context_char, bat_question_char
        del tmp_ans_range
        # torch.cuda.empty_cache()

    return answer


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="evaluate on the model")
    parser.add_argument('--config', '-c', required=False, dest='config_path', default='config/model_config.yaml')
    parser.add_argument('--output', '-o', required=False, dest='out_path')
    args = parser.parse_args()

    main(config_path=args.config_path, out_path=args.out_path)
