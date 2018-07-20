import _pickle

import numpy as np
import torch

from fastNLP.action.action import Action
from fastNLP.action.action import RandomSampler, Batchifier
from fastNLP.action.tester import POSTester
from fastNLP.modules.utils import seq_mask
from fastNLP.saver.model_saver import ModelSaver


class BaseTrainer(Action):
    """Base trainer for all trainers.
        Trainer receives a model and data, and then performs training.

        Subclasses must implement the following abstract methods:
        - prepare_input
        - mode
        - define_optimizer
        - data_forward
        - grad_backward
        - get_loss
    """

    def __init__(self, train_args):
        """
        :param train_args: dict of (key, value), or dict-like object. key is str.

        The base trainer requires the following keys:
        - epochs: int, the number of epochs in training
        - validate: bool, whether or not to validate on dev set
        - batch_size: int
        - pickle_path: str, the path to pickle files for pre-processing
        """
        super(BaseTrainer, self).__init__()
        self.n_epochs = train_args["epochs"]
        self.batch_size = train_args["batch_size"]
        self.pickle_path = train_args["pickle_path"]

        self.validate = train_args["validate"]
        self.save_best_dev = train_args["save_best_dev"]
        self.model_saved_path = train_args["model_saved_path"]

        self.model = None
        self.iterator = None
        self.loss_func = None
        self.optimizer = None

    def train(self, network):
        """General Training Steps
        :param network: a model

        The method is framework independent.
        Work by calling the following methods:
            - prepare_input
            - mode
            - define_optimizer
            - data_forward
            - get_loss
            - grad_backward
            - update
        Subclasses must implement these methods with a specific framework.
        """
        # prepare model and data
        self.model = network
        data_train, data_dev, data_test, embedding = self.prepare_input(self.pickle_path)

        # define tester over dev data
        valid_args = {"save_output": True, "validate_in_training": True, "save_dev_input": True,
                      "save_loss": True, "batch_size": self.batch_size, "pickle_path": self.pickle_path}
        validator = POSTester(valid_args)

        # main training epochs
        iterations = len(data_train) // self.batch_size
        for epoch in range(1, self.n_epochs + 1):

            # turn on network training mode; define optimizer; prepare batch iterator
            self.mode(test=False)
            self.define_optimizer()
            self.iterator = iter(Batchifier(RandomSampler(data_train), self.batch_size, drop_last=True))

            # training iterations in one epoch
            for step in range(iterations):
                batch_x, batch_y = self.batchify(data_train)

                prediction = self.data_forward(network, batch_x)

                loss = self.get_loss(prediction, batch_y)
                self.grad_backward(loss)
                self.update()

            if self.validate:
                if data_dev is None:
                    raise RuntimeError("No validation data provided.")
                validator.test(network)

                if self.save_best_dev and self.best_eval_result(validator):
                    self.save_model(network)
                    print("saved better model selected by dev")

                print("[epoch {}]".format(epoch), end=" ")
                print(validator.show_matrices())

        # finish training

    def prepare_input(self, data_path):
        """
            To do: Load pkl files of train/dev/test and embedding
        """
        data_train = _pickle.load(open(data_path + "data_train.pkl", "rb"))
        data_dev = _pickle.load(open(data_path + "data_dev.pkl", "rb"))
        data_test = _pickle.load(open(data_path + "data_test.pkl", "rb"))
        embedding = _pickle.load(open(data_path + "embedding.pkl", "rb"))
        return data_train, data_dev, data_test, embedding

    def mode(self, test=False):
        """
        Tell the network to be trained or not.
        :param test: bool
        """
        raise NotImplementedError

    def define_optimizer(self):
        """
        Define framework-specific optimizer specified by the models.
        """
        raise NotImplementedError

    def update(self):
        """
        Perform weight update on a model.

        For PyTorch, just call optimizer to update.
        """
        raise NotImplementedError

    def data_forward(self, network, x):
        """
        Forward pass of the data.
        :param network: a model
        :param x: input feature matrix and label vector
        :return: output by the models

        For PyTorch, just do "network(*x)"
        """
        raise NotImplementedError

    def grad_backward(self, loss):
        """
        Compute gradient with link rules.
        :param loss: a scalar where back-prop starts

        For PyTorch, just do "loss.backward()"
        """
        raise NotImplementedError

    def get_loss(self, predict, truth):
        """
        Compute loss given prediction and ground truth.
        :param predict: prediction label vector
        :param truth: ground truth label vector
        :return: a scalar
        """
        if self.loss_func is None:
            if hasattr(self.model, "loss"):
                self.loss_func = self.model.loss
            else:
                self.define_loss()
        return self.loss_func(predict, truth)

    def define_loss(self):
        """
            Assign an instance of loss function to self.loss_func
            E.g. self.loss_func = nn.CrossEntropyLoss()
        """
        raise NotImplementedError

    def batchify(self, data):
        """
        1. Perform batching from data and produce a batch of training data.
        2. Add padding.
        :param data: list. Each entry is a sample, which is also a list of features and label(s).
            E.g.
                [
                    [[word_11, word_12, word_13], [label_11. label_12]],  # sample 1
                    [[word_21, word_22, word_23], [label_21. label_22]],  # sample 2
                    ...
                ]
        :return batch_x: list. Each entry is a list of features of a sample. [batch_size, max_len]
                 batch_y: list. Each entry is a list of labels of a sample.  [batch_size, num_labels]
        """
        indices = next(self.iterator)
        batch = [data[idx] for idx in indices]
        batch_x = [sample[0] for sample in batch]
        batch_y = [sample[1] for sample in batch]
        batch_x = self.pad(batch_x)
        return batch_x, batch_y

    @staticmethod
    def pad(batch, fill=0):
        """
        Pad a batch of samples to maximum length.
        :param batch: list of list
        :param fill: word index to pad, default 0.
        :return: a padded batch
        """
        max_length = max([len(x) for x in batch])
        for idx, sample in enumerate(batch):
            if len(sample) < max_length:
                batch[idx] = sample + [fill * (max_length - len(sample))]
        return batch

    def best_eval_result(self, validator):
        """
        :param validator: a Tester instance
        :return: bool, True means current results on dev set is the best.
        """
        raise NotImplementedError

    def save_model(self, network):
        """
        :param network: the PyTorch model
        model_best_dev.pkl may be overwritten by a better model in future epochs.
        """
        ModelSaver(self.model_saved_path + "model_best_dev.pkl").save_pytorch(network)


class ToyTrainer(BaseTrainer):
    """
        An example to show the definition of Trainer.
    """

    def __init__(self, training_args):
        super(ToyTrainer, self).__init__(training_args)

    def prepare_input(self, data_path):
        data_train = _pickle.load(open(data_path + "/data_train.pkl", "rb"))
        data_dev = _pickle.load(open(data_path + "/data_train.pkl", "rb"))
        return data_train, data_dev, 0, 1

    def mode(self, test=False):
        self.model.mode(test)

    def data_forward(self, network, x):
        return network(x)

    def grad_backward(self, loss):
        self.model.zero_grad()
        loss.backward()

    def get_loss(self, pred, truth):
        return np.mean(np.square(pred - truth))

    def define_optimizer(self):
        self.optimizer = torch.optim.SGD(self.model.parameters(), lr=0.01)

    def update(self):
        self.optimizer.step()


class POSTrainer(BaseTrainer):
    """
    Trainer for Sequence Modeling

    """
    def __init__(self, train_args):
        super(POSTrainer, self).__init__(train_args)
        self.vocab_size = train_args["vocab_size"]
        self.num_classes = train_args["num_classes"]
        self.max_len = None
        self.mask = None
        self.best_accuracy = 0.0

    def prepare_input(self, data_path):
        """
            To do: Load pkl files of train/dev/test and embedding
        """
        data_train = _pickle.load(open(data_path + "/data_train.pkl", "rb"))
        data_dev = _pickle.load(open(data_path + "/data_train.pkl", "rb"))
        return data_train, data_dev, 0, 1

    def data_forward(self, network, x):
        """
        :param network: the PyTorch model
        :param x: list of list, [batch_size, max_len]
        :return y: [batch_size, num_classes]
        """
        seq_len = [len(seq) for seq in x]
        x = torch.Tensor(x).long()
        self.batch_size = x.size(0)
        self.max_len = x.size(1)
        self.mask = seq_mask(seq_len, self.max_len)
        y = network(x)
        return y

    def mode(self, test=False):
        if test:
            self.model.eval()
        else:
            self.model.train()

    def define_optimizer(self):
        self.optimizer = torch.optim.SGD(self.model.parameters(), lr=0.01, momentum=0.9)

    def grad_backward(self, loss):
        self.model.zero_grad()
        loss.backward()

    def update(self):
        self.optimizer.step()

    def get_loss(self, predict, truth):
        """
        Compute loss given prediction and ground truth.
        :param predict: prediction label vector, [batch_size, num_classes]
        :param truth: ground truth label vector, [batch_size, max_len]
        :return: a scalar
        """
        truth = torch.Tensor(truth)
        if self.loss_func is None:
            if hasattr(self.model, "loss"):
                self.loss_func = self.model.loss
            else:
                self.define_loss()
        loss, prediction = self.loss_func(predict, truth, self.mask, self.batch_size, self.max_len)
        # print("loss={:.2f}".format(loss.data))
        return loss

    def best_eval_result(self, validator):
        loss, accuracy = validator.matrices()
        if accuracy > self.best_accuracy:
            self.best_accuracy = accuracy
            return True
        else:
            return False


class LanguageModelTrainer(BaseTrainer):
    """
    Trainer for Language Model
    """

    def __init__(self, train_args):
        super(LanguageModelTrainer, self).__init__(train_args)

    def prepare_input(self, data_path):
        pass


if __name__ == "__name__":
    train_args = {"epochs": 1, "validate": False, "batch_size": 3, "pickle_path": "./"}
    trainer = BaseTrainer(train_args)
    data_train = [[[1, 2, 3, 4], [0]] * 10] + [[[1, 3, 5, 2], [1]] * 10]
    trainer.batchify(data=data_train)