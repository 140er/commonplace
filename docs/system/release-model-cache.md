### Disposable release model cache

- Release instances create the parent model-cache directory before starting workers. The existing launcher can then share downloaded model weights across instances, including under an isolated test home. Control and thread databases, worker homes and model sessions remain independent; real import, owner read-back, bearer refusal and mirror work still execute.
