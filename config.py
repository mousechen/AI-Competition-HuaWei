import argparse


def get_classify_config():
    parser = argparse.ArgumentParser()
    parser.add_argument('--image_size', type=tuple, default=[416, 416], help='image size')
    parser.add_argument('--batch_size', type=int, default=24, help='batch size')
    parser.add_argument('--epoch', type=int, default=50, help='epoch')

    parser.add_argument('--augmentation_flag', type=bool, default=True,
                        help='if true, use augmentation method in train set')
    parser.add_argument('--auto_aug', type=bool, default=False,
                        help='using auto augment or not.')                        
    parser.add_argument('--erase_prob', type=float, default=0.0,
                        help='probability of random erase when augmentation_flag is True')
    parser.add_argument('--gray_prob', type=float, default=0.3,
                        help='probability of gray when augmentation_flag is True')
    # 数据集划分
    parser.add_argument('--dataset_from_folder', type=bool, default=False, help='loading dataset from folder.')
    parser.add_argument('--load_split_from_file', type=str, default='', help='loading dataset split from load_split_from_file， if '' , generate online.' )
    parser.add_argument('--n_splits', type=int, default=5, help='n_splits_fold')
    parser.add_argument('--val_official', type=bool, default=False, help='only use official data in validate dataset or not.')
    parser.add_argument('--selected_fold', type=list, default=[0], help='which folds for training')
    parser.add_argument('--val_size', type=float, default=0.2, help='the ratio of val data when n_splits=1.')
    parser.add_argument('--weight_path', type=str, default='', help='the pretrained weight path.')
    # 选择使用的数据集
    parser.add_argument('--only_self', type=bool, default=False, help='only use self data or not.')
    parser.add_argument('--only_official', type=bool, default=False, help='only use official data or not.')
    # 用于训练的类别
    parser.add_argument('--selected_labels', type=list, default=None, help='labels chosen of training.')
    # cut_mix
    parser.add_argument('--cut_mix', type=bool, default=True, help='use cut mix or not.')
    parser.add_argument('--beta', type=float, default=1.0, help='beta of cut mix.')
    parser.add_argument('--cutmix_prob', type=float, default=0.5, help='cutmix probof cut mix.')
    # 多尺度
    parser.add_argument('--multi_scale', type=bool, default=True, help='use multi scale training or not.')
    parser.add_argument('--val_multi_scale', type=bool, default=True, help='use multi scale validate or not.')
    parser.add_argument('--multi_scale_size', type=list, default=[[256, 256], [288, 288], [320, 320], [352, 352], [384, 384], [416, 416]], help='multi scale choice.')
    parser.add_argument('--multi_scale_interval', type=int, default=10, help='make a scale choice every [] iterations.')
    # 稀疏度训练
    parser.add_argument('--sparsity', type=bool, default=False, help='use sparsity training or not.')
    parser.add_argument('--sparsity_scale', type=float, default=1e-2, help='sparsity scale.')
    parser.add_argument('--penalty_type', type=str, default='L1', help='penalty type.')
    # l1正则化
    parser.add_argument('--l1_regular', type=bool, default=False, help='use l1 regular or not.')
    parser.add_argument('--l1_decay', type=float, default=1e-4, help='l1 regular decay factor.')
    
    # model set
    parser.add_argument('--model_type', type=str, default='se_resnext101_32x4d',
                        help='densenet201/efficientnet-b5/se_resnext101_32x4d')
    parser.add_argument('--drop_rate', type=float, default=0, help='dropout rate in classify module')
    parser.add_argument('--bn_to_gn', type=bool, default=False, help='dropout rate in classify module')

    # model hyper-parameters
    parser.add_argument('--num_classes', type=int, default=54)
    parser.add_argument('--lr', type=float, default=3e-4, help='init lr')
    parser.add_argument('--weight_decay', type=float, default=0.0, help='weight_decay in optimizer')
    parser.add_argument('--warmup', type=bool, default=False, help='use warmup or not')
    parser.add_argument('--multiplier', type=float, default=10, help='when warm up ends, lr will be: lr * multiplier')
    parser.add_argument('--warmup_epoch', type=int, default=10, help='warm up epoch')

    # 学习率衰减策略
    parser.add_argument('--lr_scheduler', type=str, default='StepLR',
                        help='lr scheduler, StepLR/CosineLR/ReduceLR/MultiStepLR')
    parser.add_argument('--lr_step_size', type=int, default=20, help='step_size for StepLR scheduler')
    parser.add_argument('--restart_step', type=int, default=80, help='T_max for CosineAnnealingLR scheduler')
    parser.add_argument('--multi_step', type=list, default=[20, 35, 45], help='Milestone of multi_step')
    parser.add_argument('--delay_epoch', type=int, default=None, help='delay epoch (if you want to keep your lr at the begining.)')
    
    # 优化器
    parser.add_argument('--optimizer', type=str, default='Adam', help='optimizer type: Adam/SGD/RAdam/RangerLars/Ranger')
    # 损失函数
    parser.add_argument('--loss_name', type=str, default='1.0*SmoothCrossEntropy',
                        help='Select the loss function, CrossEntropy/SmoothCrossEntropy/FocalLoss/SmoothCrossEntropyHardMining')

    # 路径
    parser.add_argument('--save_path', type=str, default='./checkpoints')
    parser.add_argument('--dataset_root', type=str, default='data/huawei_data/train_data')

    config = parser.parse_args()

    return config
