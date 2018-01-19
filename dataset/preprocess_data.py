#!/usr/bin/env python
# -*- coding: utf-8 -*-

__author__ = 'han'

import os
import zipfile
import nltk
import json
import h5py
import logging
import numpy as np
from functools import reduce

logger = logging.getLogger(__name__)


class PreprocessData:
    """
    preprocess dataset and glove embedding to hdf5 files
    """

    padding = '__padding__'  # id = 0
    oov = '__oov__'  # id = 1

    __compress_option = dict(compression="gzip", compression_opts=9, shuffle=False)

    def __init__(self, global_config):
        # data config
        self.__dev_path = ''
        self.__train_path = ''
        self.__export_squad_path = ''
        self.__export_glove_path = ''
        self.__glove_path = ''
        self.__embedding_size = 300
        self.__load_config(global_config)

        # preprocess config
        self.__word2id = {}
        self.__max_context_token_len = 0
        self.__max_question_token_len = 0
        self.__max_answer_len = 0

        self.__is_exist_glove_hdf5 = os.path.exists(self.__export_glove_path)

        # data need to store in hdf5 file
        self.__glove_data = {}
        self.__squad_data = {}
        self.__glove_attr = {}
        self.__squad_attr = {}

    def __load_config(self, global_config):
        """
        load config from a dictionary, such as dataset path
        :param global_config: dictionary
        :return:
        """
        data_config = global_config['data']
        self.__train_path = data_config['dataset']['train_path']
        self.__dev_path = data_config['dataset']['dev_path']
        self.__export_squad_path = data_config['dataset_h5']
        self.__export_glove_path = data_config['embedding_h5']
        self.__glove_path = data_config['embedding_path']
        self.__embedding_size = int(global_config['model']['embedding_size'])

    def __read_json(self, path):
        """
        read json format file from raw squad text
        :param path: squad file path
        :return:
        """
        with open(path, 'r') as f:
            data = json.load(f)

        version = data['version']
        data_list_tmp = [ele['paragraphs'] for ele in data['data']]
        contexts_qas = reduce(lambda a, b: a + b, data_list_tmp)

        self.__squad_attr['name'] = 'squad-' + version
        return contexts_qas

    def __build_data(self, contexts_qas):
        """
        handle squad data to (context, question, answer_range) with word id representation
        :param contexts_qas: a context with several question-answers
        :return:
        """
        contexts_wid = []
        questions_wid = []
        answers_range_wid = []  # each answer use the [start,end) representation, all the answer horizontal concat

        for question_grp in contexts_qas:
            cur_context = question_grp['context']
            cur_qas = question_grp['qas']

            cur_context_toke = nltk.word_tokenize(cur_context)
            cur_context_ids = self.__sentence_to_id(cur_context_toke)
            self.__max_context_token_len = max(self.__max_context_token_len, len(cur_context_ids))

            for qa in cur_qas:
                cur_question = qa['question']
                cur_question_toke = nltk.word_tokenize(cur_question)
                cur_question_ids = self.__sentence_to_id(cur_question_toke)
                self.__max_question_token_len = max(self.__max_question_token_len, len(cur_question_ids))

                contexts_wid.append(cur_context_ids)
                questions_wid.append(cur_question_ids)

                # find all the answer positions
                cur_answers = qa['answers']
                self.__max_answer_len = max(self.__max_answer_len, len(cur_answers) * 2)

                cur_ans_range_ids = [0 for i in range(len(cur_answers) * 2)]
                for idx, cur_ans in enumerate(cur_answers):
                    cur_ans_text = nltk.word_tokenize(cur_ans['text'])
                    pos_s = self.__find_sublist(cur_ans_text,
                                                cur_context_toke)  # not consider find multi position in context
                    pos_e = pos_s + len(cur_ans_text)

                    cur_ans_range_ids[(idx * 2):(idx * 2 + 2)] = [pos_s, pos_e]

                answers_range_wid.append(cur_ans_range_ids)

        return {'context': contexts_wid,
                'question': questions_wid,
                'answer_range': answers_range_wid}

    def __find_sublist(self, query, base):
        """
        find sublist`s start position in a base list
        :param query: query sublist
        :param base: base list
        :return:
        """
        for i in range(len(base)):
            if base[i:(i + len(query))] == query:
                return i

        return -1

    def __sentence_to_id(self, sentence):
        """
        transform a sentence to word index id representation
        :param sentence: tokenized sentence
        :return: word ids
        """

        ids = []
        for word in sentence:
            if word in self.__word2id:
                ids.append(self.__word2id[word])
            else:
                ids.append(self.__word2id[self.oov])

        return ids

    def __handle_glove(self):
        """
        handle glove embeddings, transform text to hdf5 data or just read hdf5 file with word2id
        :return:
        """
        if not self.__is_exist_glove_hdf5:
            logger.debug("read glove from text file %s" % self.__glove_path)
            with zipfile.ZipFile(self.__glove_path, 'r') as zf:
                if len(zf.namelist()) != 1:
                    raise ValueError('glove file "%s" not recognized' % self.__glove_path)

                glove_name = zf.namelist()[0]

                words = [self.padding, self.oov]
                embeddings = [[0. for i in range(self.__embedding_size)], [0. for i in range(self.__embedding_size)]]

                word_num = 0
                with zf.open(glove_name) as f:
                    for line in f:
                        line_split = line.decode('utf-8').split(' ')
                        words.append(line_split[0])
                        embeddings.append([float(x) for x in line_split[1:]])

                        word_num += 1
                        if word_num % 10000 == 0:
                            logger.debug('handle word No.%d' % word_num)

                self.__glove_attr['word_dict_size'] = len(words)
                self.__glove_attr['embedding_size'] = self.__embedding_size
                self.__glove_data['id2word'] = np.array(words, dtype=np.str)
                self.__glove_data['id2vec'] = np.array(embeddings, dtype=np.float32)
                self.__word2id = dict(zip(words, range(len(words))))
        else:
            logger.debug("read glove from hdf5 file %s" % self.__export_glove_path)
            with h5py.File(self.__export_glove_path, 'r') as f:
                words = f['id2word']
            self.__word2id = dict(zip(words, range(len(words))))

    def __export_glove_hdf5(self):
        """
        export glove embeddings to hdf5 file
        :return:
        """
        f = h5py.File(self.__export_glove_path, 'w')
        str_dt = h5py.special_dtype(vlen=str)

        # attributes
        for attr_name in self.__glove_attr:
            f.attrs[attr_name] = self.__glove_attr[attr_name]

        # data
        for key, value in self.__glove_data.items():
            dt = value.dtype
            if type(value[0]) == np.str_:
                dt = str_dt
            data = f.create_dataset(key, value.shape, dtype=dt, **self.__compress_option)
            data[...] = value

        f.flush()
        f.close()

    def __export_squad_hdf5(self):
        """
        export squad dataset to hdf5 file
        :return:
        """
        f = h5py.File(self.__export_squad_path, 'w')

        # attributes
        for attr_name in self.__squad_attr:
            f.attrs[attr_name] = self.__squad_attr[attr_name]

        # data
        for key, value in self.__squad_data.items():
            data_grp = f.create_group(key)

            for sub_key, sub_value in value.items():
                data = data_grp.create_dataset(sub_key, sub_value.shape, dtype=sub_value.dtype, **self.__compress_option)
                data[...] = sub_value

        f.flush()
        f.close()

    def run(self):
        """
        main function to generate hdf5 file
        :return:
        """
        logger.info('handle glove file...')
        self.__handle_glove()

        logger.info('read squad json...')
        train_context_qas = self.__read_json(self.__train_path)
        dev_context_qas = self.__read_json(self.__dev_path)

        logger.info('transform word to id...')
        train_cache_nopad = self.__build_data(train_context_qas)
        dev_cache_nopad = self.__build_data(dev_context_qas)

        self.__squad_attr['train_size'] = len(train_cache_nopad['answer_range'])
        self.__squad_attr['dev_size'] = len(dev_cache_nopad['answer_range'])

        logger.info('padding id vectors...')
        self.__squad_data['train'] = {
            'context': self.__pad_sequences(train_cache_nopad['context'], maxlen=self.__max_context_token_len,
                                            padding='post'),
            'question': self.__pad_sequences(train_cache_nopad['question'], maxlen=self.__max_question_token_len,
                                             padding='post'),
            'answer_range': self.__pad_sequences(train_cache_nopad['answer_range'], maxlen=1, padding='post')}
        self.__squad_data['dev'] = {
            'context': self.__pad_sequences(dev_cache_nopad['context'], maxlen=self.__max_context_token_len,
                                            padding='post'),
            'question': self.__pad_sequences(dev_cache_nopad['question'], maxlen=self.__max_question_token_len,
                                             padding='post'),
            'answer_range': self.__pad_sequences(dev_cache_nopad['answer_range'], maxlen=self.__max_answer_len,
                                                 padding='post')}

        logger.info('export to hdf5 file...')
        self.__export_squad_hdf5()
        if not self.__is_exist_glove_hdf5:
            self.__export_glove_hdf5()

        logger.info('finished.')

    def __pad_sequences(self, sequences, maxlen=None, dtype='int32', padding='pre', truncating='pre', value=0.):
        '''
        FROM KERAS
        Pads each sequence to the same length:
        the length of the longest sequence.
        If maxlen is provided, any sequence longer
        than maxlen is truncated to maxlen.
        Truncation happens off either the beginning (default) or
        the end of the sequence.
        Supports post-padding and pre-padding (default).
        # Arguments
            sequences: list of lists where each element is a sequence
            maxlen: int, maximum length
            dtype: type to cast the resulting sequence.
            padding: 'pre' or 'post', pad either before or after each sequence.
            truncating: 'pre' or 'post', remove values from sequences larger than
                maxlen either in the beginning or in the end of the sequence
            value: float, value to pad the sequences to the desired value.
        # Returns
            x: numpy array with dimensions (number_of_sequences, maxlen)
        '''
        lengths = [len(s) for s in sequences]

        nb_samples = len(sequences)
        if maxlen is None:
            maxlen = np.max(lengths)

        # take the sample shape from the first non empty sequence
        # checking for consistency in the main loop below.
        sample_shape = tuple()
        for s in sequences:
            if len(s) > 0:
                sample_shape = np.asarray(s).shape[1:]
                break

        x = (np.ones((nb_samples, maxlen) + sample_shape) * value).astype(dtype)
        for idx, s in enumerate(sequences):
            if len(s) == 0:
                continue  # empty list was found
            if truncating == 'pre':
                trunc = s[-maxlen:]
            elif truncating == 'post':
                trunc = s[:maxlen]
            else:
                raise ValueError('Truncating type "%s" not understood' % truncating)

            # check `trunc` has expected shape
            trunc = np.asarray(trunc, dtype=dtype)
            if trunc.shape[1:] != sample_shape:
                raise ValueError('Shape of sample %s of sequence at position %s is different from expected shape %s' %
                                 (trunc.shape[1:], idx, sample_shape))

            if padding == 'post':
                x[idx, :len(trunc)] = trunc
            elif padding == 'pre':
                x[idx, -len(trunc):] = trunc
            else:
                raise ValueError('Padding type "%s" not understood' % padding)
        return x
