import torch
import tqdm
import datetime
import os
import pickle
import time
import numpy as np
import random
import shutil
import torch.nn.functional as F
from torch.utils.tensorboard import SummaryWriter
import json
import codecs

from config import get_classify_config
from solver import Solver
from utils.set_seed import seed_torch
from models.build_model import PrepareModel
from datasets.create_dataset import GetDataloader
from losses.get_loss import Loss
from utils.classification_metric import ClassificationMetric
from datasets.data_augmentation import DataAugmentation
from utils.cutmix import generate_mixed_sample
from datasets.create_dataset import multi_scale_transforms
from utils.sparsity import Sparsity, Regularization
from datasets.create_dataset import get_dataloader_from_folder


class TrainVal:
    def __init__(self, config, fold):
        """
        Args:
            config: 配置参数
            fold: 当前为第几折
        """
        self.config = config
        self.fold = fold
        self.epoch = config.epoch
        self.num_classes = config.num_classes
        self.lr_scheduler = config.lr_scheduler
        self.save_interval = 10
        self.cut_mix = config.cut_mix
        self.beta = config.beta
        self.cutmix_prob = config.cutmix_prob
        self.auto_aug = config.auto_aug

        # 多尺度
        self.image_size = config.image_size
        self.multi_scale = config.multi_scale
        self.val_multi_scale = config.val_multi_scale
        self.multi_scale_size = config.multi_scale_size
        self.multi_scale_interval = config.multi_scale_interval
        # 稀疏训练
        self.sparsity = config.sparsity
        self.sparsity_scale = config.sparsity_scale
        self.penalty_type = config.penalty_type
        self.selected_labels = config.selected_labels
        if self.auto_aug:
            print('@ Using AutoAugment.')
        if self.cut_mix:
            print('@ Using cut mix.')
        if self.multi_scale:
            print('@ Using multi scale training.')
        print('@ Using LOSS: {}'.format(config.loss_name))

        # 加载模型
        prepare_model = PrepareModel()
        self.model = prepare_model.create_model(
            model_type=config.model_type,
            classes_num=self.num_classes,
            drop_rate=config.drop_rate,
            pretrained=True,
            bn_to_gn=config.bn_to_gn
        )
        if config.weight_path:
            self.model = prepare_model.load_chekpoint(self.model, config.weight_path)
        
        # 稀疏训练
        self.sparsity_train = None
        if config.sparsity:
            print('@ Using sparsity training.')
            self.sparsity_train = Sparsity(self.model, sparsity_scale=self.sparsity_scale, penalty_type=self.penalty_type)
        
        # l1正则化
        self.l1_regular = config.l1_regular
        self.l1_decay = config.l1_decay
        if self.l1_regular:
            print('@ Using l1_regular')
            self.l1_reg_loss = Regularization(self.model, weight_decay=self.l1_decay, p=1)
            
        if torch.cuda.is_available():
            self.model = torch.nn.DataParallel(self.model)
            self.model = self.model.cuda()

        # 加载优化器
        self.optimizer = prepare_model.create_optimizer(config.model_type, self.model, config)

        # 加载衰减策略
        self.exp_lr_scheduler = prepare_model.create_lr_scheduler(
            self.lr_scheduler,
            self.optimizer,
            step_size=config.lr_step_size,
            restart_step=config.restart_step,
            multi_step=config.multi_step,
            warmup=config.warmup,
            multiplier=config.multiplier,
            warmup_epoch=config.warmup_epoch,
            delay_epoch=config.delay_epoch
        )

        # 加载损失函数
        self.criterion = Loss(config.model_type, config.loss_name, self.num_classes)

        # 实例化实现各种子函数的 solver 类
        self.device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
        self.solver = Solver(self.model, self.device)

        # log初始化
        self.writer, self.time_stamp = self.init_log()
        self.model_path = os.path.join(self.config.save_path, self.config.model_type, self.time_stamp)

        # 初始化分类度量准则类
        with open("online-service/model/label_id_name.json", 'r', encoding='utf-8') as json_file:
            self.class_names = list(json.load(json_file).values())
        self.classification_metric = ClassificationMetric(self.class_names, self.model_path)

        self.max_accuracy_valid = 0

    def train(self, train_loader, valid_loader):
        """ 完成模型的训练，保存模型与日志
        Args:
            train_loader: 训练数据的DataLoader
            valid_loader: 验证数据的Dataloader
        """
        global_step = 0
        for epoch in range(self.epoch):
            self.model.train()
            epoch += 1
            images_number, epoch_corrects = 0, 0

            tbar = tqdm.tqdm(train_loader)
            image_size = self.image_size
            l1_regular_loss = 0
            loss_with_l1_regular = 0
            for i, (images, labels) in enumerate(tbar):
                if self.multi_scale:
                    if i % self.multi_scale_interval == 0:
                        image_size = random.choice(self.multi_scale_size)
                    images = multi_scale_transforms(image_size, images, auto_aug=self.auto_aug)
                if self.cut_mix:
                    # 使用cut_mix
                    r = np.random.rand(1)
                    if self.beta > 0 and r < self.cutmix_prob:
                        images, labels_a, labels_b, lam = generate_mixed_sample(self.beta, images, labels)
                        labels_predict = self.solver.forward(images)
                        loss = self.solver.cal_loss_cutmix(labels_predict, labels_a, labels_b, lam, self.criterion)
                    else:
                        # 网络的前向传播
                        labels_predict = self.solver.forward(images)
                        loss = self.solver.cal_loss(labels_predict, labels, self.criterion)
                else:
                    # 网络的前向传播
                    labels_predict = self.solver.forward(images)
                    loss = self.solver.cal_loss(labels_predict, labels, self.criterion)
                
                if self.l1_regular:
                    current_l1_regular_loss = self.l1_reg_loss(self.model)
                    loss += current_l1_regular_loss
                    l1_regular_loss += current_l1_regular_loss.item()
                    loss_with_l1_regular += loss.item()
                self.solver.backword(self.optimizer, loss, sparsity=self.sparsity_train)

                images_number += images.size(0)
                epoch_corrects += self.model.module.get_classify_result(labels_predict, labels, self.device).sum()
                train_acc_iteration = self.model.module.get_classify_result(labels_predict, labels, self.device).mean()

                # 保存到tensorboard，每一步存储一个
                descript = self.criterion.record_loss_iteration(self.writer.add_scalar, global_step + i)
                self.writer.add_scalar('TrainAccIteration', train_acc_iteration, global_step + i)

                params_groups_lr = str()
                for group_ind, param_group in enumerate(self.optimizer.param_groups):
                    params_groups_lr = params_groups_lr + 'pg_%d' % group_ind + ': %.8f, ' % param_group['lr']

                descript = '[Train Fold {}][epoch: {}/{}][image_size: {}][Lr :{}][Acc: {:.4f}]'.format(
                    self.fold,
                    epoch,
                    self.epoch,
                    image_size,
                    params_groups_lr,
                    train_acc_iteration
                ) + descript
                if self.l1_regular:
                    descript += '[L1RegularLoss: {:.4f}][Loss: {:.4f}]'.format(current_l1_regular_loss.item(), loss.item())
                tbar.set_description(desc=descript)

            # 写到tensorboard中
            epoch_acc = epoch_corrects / images_number
            self.writer.add_scalar('TrainAccEpoch', epoch_acc, epoch)
            self.writer.add_scalar('Lr', self.optimizer.param_groups[0]['lr'], epoch)
            if self.l1_regular:
                l1_regular_loss_epoch = l1_regular_loss / len(train_loader)
                loss_with_l1_regular_epoch = loss_with_l1_regular / len(train_loader)
                self.writer.add_scalar('TrainL1RegularLoss', l1_regular_loss_epoch, epoch)
                self.writer.add_scalar('TrainLossWithL1Regular', loss_with_l1_regular_epoch, epoch)
            descript = self.criterion.record_loss_epoch(len(train_loader), self.writer.add_scalar, epoch)

            # Print the log info
            print('[Finish epoch: {}/{}][Average Acc: {:.4}]'.format(epoch, self.epoch, epoch_acc) + descript)

            # 验证模型
            val_accuracy, val_loss, is_best = self.validation(valid_loader, self.val_multi_scale)

            # 保存参数
            state = {
                'epoch': epoch,
                'state_dict': self.model.module.state_dict(),
                'max_score': self.max_accuracy_valid
            }
            self.solver.save_checkpoint(
                os.path.join(
                    self.model_path,
                    '%s_fold%d.pth' % (self.config.model_type, self.fold)
                ),
                state,
                is_best
            )

            if epoch % self.save_interval == 0:
                self.solver.save_checkpoint(
                    os.path.join(
                        self.model_path,
                        '%s_epoch%d_fold%d.pth' % (self.config.model_type, epoch, self.fold)
                    ),
                    state,
                    False
                )

            # 写到tensorboard中
            self.writer.add_scalar('ValidLoss', val_loss, epoch)
            self.writer.add_scalar('ValidAccuracy', val_accuracy, epoch)

            # 每一个epoch完毕之后，执行学习率衰减
            if self.lr_scheduler == 'ReduceLR':
                self.exp_lr_scheduler.step(metrics=val_accuracy)
            else:
                self.exp_lr_scheduler.step()
            global_step += len(train_loader)
        print('BEST ACC:{}'.format(self.max_accuracy_valid))
        source_path = os.path.join(self.model_path, 'model_best.pth')
        target_path = os.path.join(self.config.save_path, self.config.model_type, 'backup', 'model_best.pth')
        print('Copy %s to %s' % (source_path, target_path))
        shutil.copy(source_path, target_path)

    def validation(self, valid_loader, multi_scale=False):
        self.model.eval()
        labels_predict_all, labels_all = np.empty(shape=(0,)), np.empty(shape=(0,))
        epoch_loss = 0
        with torch.no_grad():
            if multi_scale:
                multi_oa = []
                for image_size in self.multi_scale_size:
                    tbar = tqdm.tqdm(valid_loader)
                    # 对于每一个尺度都计算准确率
                    for i, (_, images, labels) in enumerate(tbar):
                        images = multi_scale_transforms(image_size, images, auto_aug=False)
                        # 网络的前向传播
                        labels_predict = self.solver.forward(images)
                        loss = self.solver.cal_loss(labels_predict, labels, self.criterion)

                        epoch_loss += loss

                        # 先经过softmax函数，再经过argmax函数
                        labels_predict = F.softmax(labels_predict, dim=1)
                        labels_predict = torch.argmax(labels_predict, dim=1).detach().cpu().numpy()

                        labels_predict_all = np.concatenate((labels_predict_all, labels_predict))
                        labels_all = np.concatenate((labels_all, labels))

                        descript = '[Valid][Loss: {:.4f}]'.format(loss)
                        tbar.set_description(desc=descript)

                    classify_report, my_confusion_matrix, acc_for_each_class, oa, average_accuracy, kappa = \
                        self.classification_metric.get_metric(
                            labels_all,
                            labels_predict_all
                        )
                    multi_oa.append(oa)
                oa = np.asarray(multi_oa).mean()
            else:
                tbar = tqdm.tqdm(valid_loader)
                for i, (_, images, labels) in enumerate(tbar):
                    # 网络的前向传播
                    labels_predict = self.solver.forward(images)
                    loss = self.solver.cal_loss(labels_predict, labels, self.criterion)

                    epoch_loss += loss

                    # 先经过softmax函数，再经过argmax函数
                    labels_predict = F.softmax(labels_predict, dim=1)
                    labels_predict = torch.argmax(labels_predict, dim=1).detach().cpu().numpy()

                    labels_predict_all = np.concatenate((labels_predict_all, labels_predict))
                    labels_all = np.concatenate((labels_all, labels))

                    descript = '[Valid][Loss: {:.4f}]'.format(loss)
                    tbar.set_description(desc=descript)

                classify_report, my_confusion_matrix, acc_for_each_class, oa, average_accuracy, kappa = \
                    self.classification_metric.get_metric(
                        labels_all,
                        labels_predict_all
                    )
                            
            if oa > self.max_accuracy_valid:
                is_best = True
                self.max_accuracy_valid = oa
                if not self.selected_labels:
                    # 只有在未指定训练类别时才画混淆矩阵，否则会出错
                    self.classification_metric.draw_cm_and_save_result(
                        classify_report,
                        my_confusion_matrix,
                        acc_for_each_class,
                        oa,
                        average_accuracy,
                        kappa
                    )
            else:
                is_best = False

            print('OA:{}, AA:{}, Kappa:{}'.format(oa, average_accuracy, kappa))

            return oa, epoch_loss / len(tbar), is_best

    def init_log(self):
        # 保存配置信息和初始化tensorboard
        TIMESTAMP = "log-{0:%Y-%m-%dT%H-%M-%S}".format(datetime.datetime.now())
        log_dir = os.path.join(self.config.save_path, self.config.model_type, TIMESTAMP)
        writer = SummaryWriter(log_dir=log_dir)
        with codecs.open(os.path.join(log_dir, 'config.json'), 'w', "utf-8") as json_file:
            json.dump({k: v for k, v in config._get_kwargs()}, json_file, ensure_ascii=False)

        seed = int(time.time())
        seed_torch(seed)
        with open(os.path.join(log_dir, 'seed.pkl'), 'wb') as f:
            pickle.dump({'seed': seed}, f, -1)

        return writer, TIMESTAMP


if __name__ == "__main__":
    config = get_classify_config()
    data_root = config.dataset_root
    folds_split = config.n_splits
    test_size = config.val_size
    only_self = config.only_self
    only_official = config.only_official
    multi_scale = config.multi_scale
    val_multi_scale = config.val_multi_scale
    val_official = config.val_official
    load_split_from_file = config.load_split_from_file
    selected_labels = config.selected_labels
    auto_aug = config.auto_aug
    mean = (0.485, 0.456, 0.406)
    std = (0.229, 0.224, 0.225)
    if config.augmentation_flag:
        transforms = DataAugmentation(config.erase_prob, full_aug=True, gray_prob=config.gray_prob)
    else:
        transforms = None
    if config.dataset_from_folder:
        train_dataloaders, val_dataloaders = get_dataloader_from_folder(
            data_root, 
            config.image_size, 
            transforms, 
            mean, 
            std, 
            config.batch_size, 
            only_official, 
            only_self, 
            multi_scale, 
            config.auto_aug
            )
        train_dataloaders, val_dataloaders = [train_dataloaders], [val_dataloaders]
    else:
        get_dataloader = GetDataloader(
            data_root, 
            folds_split=folds_split, 
            test_size=test_size, 
            only_self=only_self, 
            only_official=only_official, 
            selected_labels=selected_labels,
            val_official=val_official,
            load_split_from_file=load_split_from_file,
            auto_aug=auto_aug
            )
        train_dataloaders, val_dataloaders = get_dataloader.get_dataloader(config.batch_size, config.image_size, mean, std,
                                                                        transforms=transforms, multi_scale=multi_scale, val_multi_scale=val_multi_scale)

    for fold_index, [train_loader, valid_loader] in enumerate(zip(train_dataloaders, val_dataloaders)):
        if fold_index in config.selected_fold:
            train_val = TrainVal(config, fold_index)
            train_val.train(train_loader, valid_loader)
