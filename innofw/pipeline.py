# standard library
import logging.config
from pathlib import Path
from typing import Optional
from uuid import uuid4

import hydra
from lovely_numpy import lo
from omegaconf import DictConfig
from pytorch_lightning import seed_everything

from innofw import InnoModel
from innofw.constants import SegDataKeys
from innofw.constants import Stages
from innofw.utils import get_project_root
from innofw.utils.defaults import default_model_for_datamodule
from innofw.utils.framework import get_augmentations
from innofw.utils.framework import get_callbacks
from innofw.utils.framework import get_ckpt_path
from innofw.utils.framework import get_datamodule
from innofw.utils.framework import get_losses
from innofw.utils.framework import get_model
from innofw.utils.framework import get_obj
from innofw.utils.framework import get_optimizer
from innofw.utils.framework import map_model_to_framework
from innofw.utils.getters import get_a_learner
from innofw.utils.getters import get_log_dir
from innofw.utils.getters import get_trainer_cfg
from innofw.utils.print_config import print_config_tree

# third party packages
# local modules


def run_pipeline(
    cfg: DictConfig,
    train=True,
    test=False,
    predict=False,
    log_root: Optional[Path] = None,
) -> float:
    print_config_tree(cfg)

    try:
        experiment_name = cfg.experiment_name
    except (
        ValueError
    ):  # hydra config not set, happens when run_pipeline is called from tests
        experiment_name = str(uuid4()).split("-")[0]

    if cfg.get("random_seed"):
        seed_everything(cfg.random_seed, workers=True)

    stage = "train" if train else "test" if test else "infer"
    project = cfg.get("project")
    data_stage = Stages.predict if predict else Stages.train
    trainer_cfg = get_trainer_cfg(cfg)
    task = cfg.get("task")
    # Model
    if "models" in cfg:
        model = get_model(cfg.models, trainer_cfg)
    else:
        datamodule = cfg.datasets.get("_target_")
        if datamodule is None:
            raise ValueError(
                "wrong configuration: no model and dataset _target_ specified"
            )
        else:
            model = hydra.utils.instantiate(
                default_model_for_datamodule(task, datamodule)
            )

    try:
        framework = map_model_to_framework(model)  # todo: add type
    except NotImplementedError as e:
        logging.info(e)
        return -1

    # weights initialization
    initializations = get_obj(
        cfg, "initializations", task, framework, _recursive_=False
    )
    augmentations_train = get_augmentations(cfg.get("augmentations_train"))
    augmentations_val = get_augmentations(cfg.get("augmentations_val"))
    augmentations_test = get_augmentations(cfg.get("augmentations_test"))
    augmentations = {
        "train": augmentations_train,
        "val": augmentations_val,
        "test": augmentations_test,
    }

    metrics = get_obj(cfg, "metrics", task, framework)
    optimizers = get_optimizer(cfg, "optimizers", task, framework)
    schedulers = get_obj(cfg, "schedulers", task, framework)
    datamodule = get_datamodule(
        cfg.datasets,
        framework,
        task=task,
        stage=data_stage,
        augmentations=augmentations,
        batch_size=cfg.get("batch_size"),
    )
    losses = get_losses(cfg, task, framework)
    callbacks = get_callbacks(
        cfg,
        task,
        framework,
        metrics=metrics,
        losses=losses,
        datamodule=datamodule,
    )

    log_dir = get_log_dir(project, stage, experiment_name, log_root=log_root)

    # todo: water erosion
    # model
    #
    logger = hydra.utils.instantiate(cfg.get("loggers"))

    # wrap the model
    model_params = {
        "model": model,
        "task": task,
        "trainer_cfg": trainer_cfg,
        "optimizers_cfg": optimizers,
        "schedulers_cfg": schedulers,
        "losses": losses,
        "callbacks": callbacks,
        "initializations": initializations,
        "log_dir": log_dir,
        "experiment": experiment_name,
        "project": project,
        "stop_param": cfg.get("stop_param"),
        "weights_path": cfg.get("weights_path"),
        "weights_freq": cfg.get("weights_freq"),
        "logger": logger,
    }
    inno_model = InnoModel(**model_params)
    result = None

    stages = []
    if train:
        stages.append(Stages.train)
    if test:
        stages.append(Stages.test)
    if predict:
        stages.append(Stages.predict)

    stage_to_func = {
        Stages.test: inno_model.test,
        Stages.predict: inno_model.predict,
        Stages.train: inno_model.train,
    }

    if "extra" in cfg and "active_learning" in cfg.extra:
        a_learner = get_a_learner(cfg, inno_model, datamodule)
        stage_to_func[Stages.train] = a_learner.run

    try:
        # print sample data
        datamodule.setup_train_test_val()
        train_dl = datamodule.train_dataloader()
        val_dl = datamodule.val_dataloader()
        print(
            "train sample stats"
        )  # todo: refactor(through logging; make it optional and make it general, I think such method should be in the datamodule's class)
        # for instance:
        """
            dm.log_sample_stats() # this method should be capable of logging results to file
            dm.save_n_samples(path)  # in case of tabular data it saves csv file, in case of images it save grid, in case of audio it saves one audio file with multiple recordings
            dm.validate_sample(max_val=255, min_val=0, shape=(3, 256, 256), mask_unique_vals=2) 
        """
        logging.config.fileConfig(get_project_root() / "logging.conf")
        LOGGER = logging.getLogger(__name__)

        LOGGER.info(lo(next(iter(train_dl))[SegDataKeys.image]))
        LOGGER.info(lo(next(iter(train_dl))[SegDataKeys.label]))
        LOGGER.info("val sample stats")
        LOGGER.info(lo(next(iter(val_dl))[SegDataKeys.image]))
        LOGGER.info(lo(next(iter(val_dl))[SegDataKeys.label]))
    except:
        pass

    ckpt_path = get_ckpt_path(cfg)
    logging.info(f"Using checkpoint: {ckpt_path}")
    for stage in stages:
        result = stage_to_func[stage](datamodule, ckpt_path=ckpt_path)

    return result