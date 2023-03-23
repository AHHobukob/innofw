import os

from omegaconf import OmegaConf

TASK = None


def setup_wandb(cfg):
    """Function to enable Weights and Biases logger"""
    if "wandb" not in cfg:
        return

    wandb_cfg = cfg.get("wandb")
    if wandb_cfg and wandb_cfg.get("enable"):
        os.environ["WANDB_DISABLED"] = "false"
        os.environ["WANDB_MODE"] = "online"

        cfg_container = OmegaConf.to_container(
            cfg, resolve=True, throw_on_missing=True
        )
        import wandb

        model_name = cfg.models._target_.split(".")[-1]
        encoder_name = cfg.models.encoder_name
        # loss_name = cfg.losses.implementations.torch.JaccardLoss.object._target_.split('.')[-1]
        optim_name = cfg.optimizers._target_.split(".")[-1]
        lr = cfg.optimizers.lr
        # scheduler = cfg.schedulers._target_.split('.')[-1][:5]
        # -{scheduler}
        # {loss_name}-
        run_name = f"{model_name}-{encoder_name}-{optim_name}-{lr}".lower()

        run = wandb.init(
            entity=wandb_cfg.entity,
            # group=wandb_cfg.group,
            project=cfg.get("project"),
            config=cfg_container,  # type: ignore
            name=run_name,
        )

        # os.environ["WANDB_DIR"] = str(run_save_path)
        return run


def setup_clear_ml(cfg):
    if "clear_ml" not in cfg:
        return
    clear_ml_cfg = cfg.get("clear_ml")
    if clear_ml_cfg and clear_ml_cfg.get("enable"):
        from clearml import Task

        task = Task.init(
            project_name=cfg["project"], task_name=cfg["experiment_name"]
        )
        setup_agent(task, clear_ml_cfg)
        global TASK
        TASK = task
        task.connect(OmegaConf.to_container(cfg, resolve=True))
        return task


def setup_agent(task, cfg):
    if cfg["queue"]:
        task.execute_remotely(queue_name=cfg["queue"])
