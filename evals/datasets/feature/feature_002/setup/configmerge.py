def to_env(prefix, cfg):
    return {f'{prefix}_{k}': v for k, v in cfg.items()}


def merge_configs(*configs):
    raise NotImplementedError
