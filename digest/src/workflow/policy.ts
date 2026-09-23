// Shared by the workflow's model retry policy and the repair activity, which records a fault as its
// answer only on the last attempt.
export const MODEL_MAX_ATTEMPTS = 3;
// The network policy's attempts; a feed records its failure as a health row only on the last one.
export const NETWORK_MAX_ATTEMPTS = 3;
// The Python worker's queue (digest/python/worker.py): the activities that stay in Python.
export const PYTHON_TASK_QUEUE = "python";
