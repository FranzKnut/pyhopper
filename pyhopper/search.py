# Copyright 2022 Mathias Lechner and the PyHopper team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import os.path

from .cache import EvaluationCache
from .callbacks import History
from .callbacks.callbacks import CheckpointCallback
from .parameters import (
    FloatParameter,
    IntParameter,
    ChoiceParameter,
    CustomParameter,
    Parameter,
    PowerOfIntParameter,
    LogSpaceFloatParameter,
)
from .parallel import execute, TaskManager, SignalListener
import numpy as np
from typing import Union, Optional, Any, Tuple, Sequence
from types import FunctionType
from enum import Enum
import time

from .run_context import ScheduledRun, RunContext
from .utils import (
    parse_timeout,
    sanitize_bounds,
    infer_shape,
    time_to_pretty_str,
    steps_to_pretty_str,
    ParamInfo,
    CandidateType,
    merge_dicts,
    convert_to_list,
    convert_to_checkpoint_path,
    load_dict,
    store_dict,
)


def register_int(
    lb: Optional[Union[int, float, np.ndarray]] = None,
    ub: Optional[Union[int, float, np.ndarray]] = None,
    init: Optional[Union[int, float, np.ndarray]] = None,
    multiple_of: Optional[int] = None,
    power_of: Optional[int] = None,
    shape: Optional[Union[int, Tuple]] = None,
    seeding_fn: Optional[callable] = None,
    mutation_fn: Optional[callable] = None,
) -> IntParameter:
    """Creates a new integer parameter

    :param lb: Lower bound of the parameter.
    :param ub: Upper bound of the parameter. If None, the `lb` argument will be used as upper bound with a lower bound of 0.
    :param init: Initial value of the parameter. If None it will be randomly sampled
    :param multiple_of: Setting this value to a positive integer enforces the sampled values of this parameter to be a mulitple of `multiple_of`.
    :param shape: For NumPy array type parameters, this argument must be set to a tuple containing the shape of the np.ndarray
    :param mutation_fn: Setting this argument to a callable overwrites the default local sampling strategy. The callback gets called with the value
        of the the current best solution as argument and returns a mutated value
    :param seeding_fn: Setting this argument to a callable overwrites the default random seeding strategy
    :return:
    """
    if lb is None and ub is None:
        # Unbounded int is actually a 32-bit integer
        lb = np.iinfo(np.int32).min
        ub = np.iinfo(np.int32).max
    lb, ub = sanitize_bounds(lb, ub)
    param_shape = infer_shape(shape, init, lb, ub) if shape is None else shape
    if power_of is not None:
        if power_of not in [1, 2]:
            raise ValueError(
                f"Power of {power_of} integers are currently not supported (only power 2 integers)."
            )
        return PowerOfIntParameter(
            shape,
            lb,
            ub,
            init,
            power_of,
            multiple_of,
            mutation_fn,
            seeding_fn,
        )
    param = IntParameter(
        param_shape,
        lb,
        ub,
        init,
        multiple_of,
        mutation_fn,
        seeding_fn,
    )
    return param


def register_custom(
    seeding_fn: Optional[callable] = None,
    mutation_fn: Optional[callable] = None,
    init: Any = None,
) -> CustomParameter:
    if seeding_fn is None and init is None:
        raise ValueError(
            f"Could not create custom parameter, must either provide an initial value or a seeding strategy function"
        )
    if init is None:
        init = seeding_fn()
    param = CustomParameter(init, mutation_fn, seeding_fn)
    return param


def register_choice(
    options: list,
    init: Optional[Any] = None,
    is_ordinal: bool = False,
    mutation_fn: Optional[FunctionType] = None,
    seeding_fn: Optional[FunctionType] = None,
) -> ChoiceParameter:
    """Creates a new choice parameter

    :param options: List containing the possible values of this parameter
    :param init: Initial value of the parameter. If None it will be randomly sampled.
    :param is_ordinal: Flag indicating whether two neighboring list items ordered or not. If True, in the local sampling stage list items neighboring the current best value will be preferred. For sets with a natural ordering it is recommended to set this flag to True.
    :param mutation_fn: Setting this argument to a callable overwrites the default local sampling strategy. The callback gets called with the value
        of the the current best solution as argument and returns a mutated value
    :param seeding_fn: Setting this argument to a callable overwrites the default random seeding strategy
    :return:
    """
    if len(options) == 0:
        raise ValueError("List with possible values must not be empty.")
    param = ChoiceParameter(options, init, is_ordinal, mutation_fn, seeding_fn)
    return param


def register_float(
    lb: Optional[Union[int, float, np.ndarray]] = None,
    ub: Optional[Union[int, float, np.ndarray]] = None,
    fmt: Optional[str] = None,
    init: Optional[Union[int, float, np.ndarray]] = None,
    log: Union[bool] = None,
    precision: Optional[int] = None,
    shape: Optional[Union[int, Tuple]] = None,
    mutation_fn: Optional[FunctionType] = None,
    seeding_fn: Optional[FunctionType] = None,
) -> FloatParameter:
    """Creates a new floating point parameter

    :param lb: Lower bound of the parameter. If both `lb` and `ub` are None, this parameter will be unbounded (usually not recommended).
    :param ub: Upper bound of the parameter. If None, the `lb` argument will be used as upper bound with a lower bound of 0.
    :param init: Initial value of the parameter. If None it will be randomly sampled
    :param fmt: Format string as syntactic sugar for setting both log and precision.
        fmt="0.2f" refers to parameter with linear search space and 2 decimal digts precision.
        fmt="0.1g" refers to a parameter with logarithmic search space and 1 significant digit precision
    :param shape: For NumPy array type parameters, this argument must be set to a tuple containing the shape of the np.ndarray
    :param log: Whether to use logarithmic or linearly scaling of the parameter.
        Defaults to False which searches the space linearly.
        If True, a logarithmic scaling is applied to the search space of this variable
    :param precision: Rounds the values to the specified significant digits.
        Defaults to None meaning that no rounding is applied
    :param mutation_fn: Setting this argument to a callable overwrites the default local sampling strategy. The callback gets called with the value
        of the the current best solution as argument and returns a mutated value
    :param seeding_fn: Setting this argument to a callable overwrites the default random seeding strategy
    """
    lb, ub = sanitize_bounds(lb, ub)
    if log is not None and fmt is not None:
        raise ValueError(f"Cannot specify `log` and `fmt` at the same time.")
    if precision is not None and fmt is not None:
        raise ValueError(f"Cannot specify `log` and `fmt` at the same time.")

    if fmt is not None:
        # simple but non-pedantic parsing of the format string
        if fmt.endswith("g"):
            log = True
        fmt = fmt.replace(":", "").replace(".", "").replace("g", "").replace("f", "")
        try:
            precision = int(fmt)
        except ValueError as e:
            raise ValueError(
                f"Could not parse format string '{fmt}'. Valid examples are ':0.3f', '0.1g' (error details: {str(e)})"
            )

    if log and (lb is None or ub is None):
        raise ValueError(
            "Logarithmically distributed mode without bounds is not supported. Please specify lower and upper bound."
        )
    if log and (lb <= 0 or ub <= 0):
        raise ValueError(
            "Both bounds for logarithmically distributed parameter must be positive."
        )

    param_shape = infer_shape(init, lb, ub) if shape is None else shape
    if log:
        return LogSpaceFloatParameter(
            param_shape, lb, ub, init, precision, mutation_fn, seeding_fn
        )
    param = FloatParameter(
        param_shape,
        lb,
        ub,
        init,
        precision,
        mutation_fn,
        seeding_fn,
    )
    return param


class Search:
    def __init__(self, *args: Union[dict, Sequence[dict]], **kwargs):
        """
        Creates a new search object

        :param args: dict defining the search space. If multiple dicts are provided the dicts will be merged.
        :param kwargs: key-value pairs defining the search space. Will be merged with the numbered arguments if some are provided
        """
        parameters = {}
        if len(args) > 0:
            parameters = merge_dicts(*args)

        parameters = merge_dicts(parameters, kwargs)

        self._params = parameters
        self._best_solution = self._get_initial_solution(self._params)

        self._free_param_count = self._count_free_parameters()
        self._best_f = None
        self._f_cache = EvaluationCache()
        self._run_context = None
        self._manually_queued_candidates = []

        self._signal_listener = SignalListener()
        self._history = History()
        self._checkpoint_path = None
        self._caught_exception = False

    def __iadd__(self, other):
        self.enqueue(other)
        return self

    def __setitem__(self, key, value):
        self._params[key] = value
        self._free_param_count = self._count_free_parameters()
        if self._best_f is None:
            # Special case if setitem is called before run
            self._best_solution[key] = self._get_initial_solution(value)

    def _get_initial_solution(self, param):
        if isinstance(param, Parameter):
            return param.initial_value
        elif isinstance(param, dict):
            return {k: self._get_initial_solution(v) for k, v in param.items()}
        elif isinstance(param, list):
            return [self._get_initial_solution(v) for v in param]
        else:
            return param

    # def overwrite_best(self, candidate: dict, f: Optional[float] = None) -> None:
    #     """Overwrites the current best solution with the provided parameter and objective function value
    #
    #     :param candidate: Parameter values that will be set as current best candidate
    #     :param f: Objective function value that will be set as the current best value
    #     """
    #     for k, v in self._params.items():
    #         cv = candidate.get(k)
    #         if cv is not None:
    #             self._best_solution[k] = cv
    #         else:
    #             init = self._best_solution.get(k)
    #             if init is None:
    #                 raise ValueError(f"Parameter '{k}' has no initial value.")
    #     self._best_f = f

    def _count_free_parameters(self):
        return self._count_free_parameters_rec(self._params)

    def _count_free_parameters_rec(self, node):
        free_params = 0
        if isinstance(node, Parameter):
            free_params = 1
        elif isinstance(node, dict):
            for k, v in node.items():
                free_params += self._count_free_parameters_rec(v)
        elif isinstance(node, list):
            for v in node:
                free_params += self._count_free_parameters_rec(v)
        return free_params

    def forget_cached(self, candidate: dict):
        """Removes the given parameter candidate from the evaluation cache. This might be useful if a parameter value should be reevaluated.

        :param candidate: Parameter candidate to be wiped from the evaluation cache
        """
        self._f_cache.forget(candidate)

    def clear_cache(self):
        """Forgets all values of already evaluated parameters."""
        self._f_cache.clear()

    def _enqueue_rec(self, node_best, node_candidate):
        if isinstance(node_best, dict):
            if not isinstance(node_candidate, dict):
                raise ValueError(
                    f"Parameter guess '{node_candidate}' does not match the tree structure '{node_best.keys()}' registered in 'Search.__init__'"
                )
            for k in node_candidate.keys():
                if k not in node_best.keys():
                    raise ValueError(
                        f"Parameter guess for '{k}' was provided but has not been registered in 'Search.__init__'. You can "
                        f"register '{k}' as a dummy parameter by passing '...= Search({k}=None, ...)'."
                    )

            candidate = {}
            for k, v in node_best.items():
                if k in node_candidate.keys():
                    candidate[k] = self._enqueue_rec(node_best[k], node_candidate[k])
                else:
                    candidate[k] = node_best[k]
            return candidate
        elif isinstance(node_best, list):
            candidate = []
            for i in range(len(node_best)):
                if len(node_candidate) > i:
                    candidate.append(self._enqueue_rec(node_best[i], node_candidate[i]))
                else:
                    candidate.append(node_best[i])
            return candidate
        else:
            return node_candidate

    def enqueue(self, candidate: dict) -> None:
        """
        Queues a guess for the optimal parameters to the search queue.

        :param candidate: dict representing a subset of the parameters assigned to a value
        """
        added_candidate = self._enqueue_rec(self._best_solution, candidate)

        self._manually_queued_candidates.append(added_candidate)

    # def sweep(self, name: str, candidate_values: list) -> None:
    #     """
    #
    #     :param name:
    #     :param candidate_values:
    #     """
    #     if name not in self._params.keys():
    #         raise ValueError(f"Could not find '{name}' in set of registered parameters")
    #     for value in candidate_values:
    #         added_candidate = {}
    #         for k, v in self._params.items():
    #             if k == name:
    #                 added_candidate[k] = value
    #                 continue
    #             init = self._best_solution.get(k)
    #             if init is not None:
    #                 added_candidate[k] = init
    #             else:
    #                 raise ValueError(f"Parameter '{k}' has no initial value.")
    #         self._manually_queued_candidates.append(added_candidate)

    # def _fill_missing_init_values(self, node=None, node_best=None):
    #     if node is None:
    #         node = self._params
    #         node_best = self._best_solution
    #
    #     if isinstance(node, Parameter):
    #
    #     elif isinstance(node, dict):
    #         return {k: self.sample_solution(v) for k, v in node.items()}
    #     elif isinstance(node, list):
    #         return [self.sample_solution(v) for v in node]
    #     else:

    def sample_solution(self, node=None):
        if node is None:
            node = self._params
        if isinstance(node, Parameter):
            return node.sample()
        elif isinstance(node, dict):
            return {k: self.sample_solution(v) for k, v in node.items()}
        elif isinstance(node, list):
            return [self.sample_solution(v) for v in node]
        else:
            return node

    def mutate_from_best(self, temperature, node=None, best_node=None, bitmask=None):

        if node is None:
            temperature = float(np.clip(temperature, 0, 1))
            node = self._params
            best_node = self._best_solution
            # With decreasing temperature we resample/mutate fewer parameters
            amount_to_mutate = int(
                max(round(temperature * self.free_param_count), 1)
            )  # at least 1, at most all
            bitmask = [i < amount_to_mutate for i in range(self.free_param_count)]
            np.random.default_rng().shuffle(bitmask)

        if isinstance(node, Parameter):
            p = bitmask.pop()
            # consume one bit -> tells us if we should mutate or not
            return node.mutate(best_node, temperature=temperature) if p else best_node
        elif isinstance(node, dict):
            return {
                k: self.mutate_from_best(
                    temperature, node=node[k], best_node=best_node[k], bitmask=bitmask
                )
                for k, v in node.items()
            }
        elif isinstance(node, list):
            return [
                self.mutate_from_best(
                    temperature, node=node[i], best_node=best_node[i], bitmask=bitmask
                )
                for i in range(len(node))
            ]
        else:
            return node

    def _submit_candidate(self, objective_function, candidate_type, candidate, kwargs):
        param_info = ParamInfo(candidate_type, sampled_at=time.time())
        for c in self._run_context.callbacks:
            c.on_evaluate_start(candidate, param_info)

        self._f_cache.stage(candidate)
        if self._run_context.task_executor is None:
            candidate_result = execute(
                objective_function,
                candidate,
                self._run_context.pruner,
                kwargs,
            )
            param_info.finished_at = time.time()
            self._async_result_ready(candidate, param_info, candidate_result)
        else:
            self._run_context.task_executor.submit(
                objective_function,
                candidate,
                param_info,
                self._run_context.pruner,
                kwargs,
            )

    def _wait_for_one_free_executor(self):
        if self._run_context.task_executor is not None:
            if self._run_context.task_executor.is_full:
                # Queue is full, let's wait at least until 1 task is done before submitting this one
                self._run_context.task_executor.wait_for_first_to_complete()

            for (
                candidate,
                param_info,
                candidate_result,
            ) in self._run_context.task_executor.iterate_done_tasks():
                self._async_result_ready(candidate, param_info, candidate_result)

    def _wait_for_all_running_jobs(self):
        if self._run_context.task_executor is not None:
            self._run_context.task_executor.wait_for_all_to_complete()
            for (
                candidate,
                param_info,
                candidate_result,
            ) in self._run_context.task_executor.iterate_done_tasks():
                self._async_result_ready(candidate, param_info, candidate_result)

    def _async_result_ready(self, candidate, param_info, candidate_result):
        if candidate_result.error is not None:
            if not self._caught_exception:
                self._caught_exception = True
                self._shutdown_worker_processes()
                print("Remote process caught exception in objective function: ")
                print("======================================================")
                print(candidate_result.error)
                print(
                    "======================================================", flush=True
                )
                raise ValueError("Pyhopper - Remote process caught exception")
            return
        if candidate_result.is_nan and not self._run_context.ignore_nans:
            raise ValueError(
                "NaN returned in objective function. If NaNs should be ignored (treated as pruned evaluations) pass 'ignore_nans=True' argument to 'run'"
            )
        self._f_cache.commit(candidate, candidate_result.value)
        if self._run_context.pruner is not None and not candidate_result.is_nan:
            # If the result is NaN we should not tell the Pruner object
            # TODO: Maybe we should catch if the user does not call "should_prune" or the of is not a generator
            # if candidate_result.intermediate_results is None:
            #     raise ValueError(
            #         "A Pruner was passed to `run` but the objective function is not a generator"
            #     )
            self._run_context.pruner.append(
                candidate_result.intermediate_results, candidate_result.was_pruned
            )

        if candidate_result.was_pruned:
            param_info.is_pruned = True
            for c in self._run_context.callbacks:
                c.on_evaluate_pruned(candidate, param_info)
            return
        if candidate_result.is_nan:
            param_info.is_nan = True
            for c in self._run_context.callbacks:
                c.on_evaluate_nan(candidate, param_info)
            return

        for c in self._run_context.callbacks:
            c.on_evaluate_end(candidate, candidate_result.value, param_info)

        if (
            self._best_f is None
            or (
                self._run_context.direction == "max"
                and candidate_result.value > self._best_f
            )
            or (
                self._run_context.direction == "min"
                and candidate_result.value < self._best_f
            )
        ):
            # new best solution
            self._best_solution = candidate
            self._best_f = candidate_result.value
            for c in self._run_context.callbacks:
                c.on_new_best(self._best_solution, self._best_f, param_info)

    def _shutdown_worker_processes(self):
        # This is actually not needed but let's keep it for potential future use
        if self._run_context.task_executor is not None:
            self._run_context.task_executor.shutdown()
            import psutil
            import signal

            try:
                parent = psutil.Process(os.getpid())
                children = parent.children(recursive=True)
                for process in children:
                    process.send_signal(signal.SIGTERM)
            except psutil.NoSuchProcess:
                pass

    def _force_termination(self):
        self._shutdown_worker_processes()
        import sys

        sys.exit(-1)

    def run(
        self,
        objective_function,
        direction: str = "maximize",
        timeout: Union[int, float, str, None] = None,
        steps: Union[int, str, None] = None,
        endless_mode: bool = False,
        seeding_steps: Optional[int] = None,
        seeding_timeout: Union[int, float, str, None] = None,
        seeding_ratio: Optional[float] = 0.25,
        pruner=None,
        n_jobs=1,
        quiet=False,
        ignore_nans=False,
        mp_backend="auto",
        enable_rejection_cache=True,
        callbacks: Union[callable, list, None] = None,
        start_temperature: float = 1,
        end_temperature: float = 0,
        kwargs=None,
        checkpoint_path=None,
        overwrite_checkpoint=False,
        keep_history=True,
    ):
        """Starts the hyperparameter tuning process.

        :param objective_function: The objective function that should be optimized.
            Can be a generator function that yields estimates of the true objective function to prune unpromising candidates early on.
        :param direction: String defining if the objective function should be minimized or maximize
            (admissible values are 'min','minimize', or 'max','maximize')
        :param timeout: Search timeout in seconds or a string, e.g., "1h 30min", "4d 12h".
        :param steps: Number of search steps. Must be left None if a value for `timeout` is provided.
        :param endless_mode: Setting this argument to True runs the search until the user interrupts (via CTRL+C). Must be left Noen if a value for `timeout` or `steps` is provided
        :param seeding_steps:
        :param seeding_timeout:
        :param seeding_ratio:
        :param pruner: A `pyhopper.pruners.Pruner` instance that cancels the evaluation of unpromising candidates.
            If a pruner is provided, the objective function must be a generator that yield intermediate estimates of the
            objective value.
        :param n_jobs: Number of parallel execution process. `n_jobs=-1` spawns a process for each CPU core,
            `n_jobs="per-gpu"` spawns a process for each GPU (and sets the visibility of the GPU in the environment variables accordingly).
        :param quiet: If True, then a progress bar is shown during the search and a short summary at the end.
        :param ignore_nans: If True, NaN (not-a-number) values returned by the objective function will be ignored
            (parameters will be treated the same as pruned parameter values). If False (default), NaN values returned by the
            objective function will raise an exception (this might be important for finding bugs in the objective function)
        :param mp_backend:
        :param enable_rejection_cache: If True (default), generated parameter candidates will be filtered by removing
             duplicates (= don't evaluate a parameter if the same parameter has been already evaluated before).
             If False, no such check/filtering is performed.
        :param callbacks: A list of `pyhopper.callbacks.Callback` instances that will be called throughout the search.
        :param start_temperature:
        :param end_temperature:
        :param kwargs: A dict that will be passed to the objective function as named arguments.
        :param checkpoint_path: A file or directory for storing the intermediate state of the search.
            If `checkpoint_path` is an existing directory, Pyhopper will save the state in a new file "pyhopper_run_XXXXX.ckpt".
        :param overwrite_checkpoint:  If True, the file provided by the `checkpoint_path` argument will be overwritten if it already exists.
            If False (default), Pyhopper will try to restore and continue the search from the checkpoint provided by the `checkpoint_path`.
            If the file provided in the `checkpoint_path` argument does not exist, this argument will be ignored.
        :param keep_history: If True (default), the all evaluated candidate parameters and correspondign objective values
            will be stored in the `pyhopper.Search.history` property.
            If False, no such history is created (this might save some memory).
        :return: A `dict` containing the best found parameters
        """
        if kwargs is None:
            kwargs = {}

        self._caught_exception = False
        schedule = ScheduledRun(
            steps,
            timeout,
            endless_mode,
            seeding_steps=seeding_steps,
            seeding_timeout=seeding_timeout,
            seeding_ratio=seeding_ratio,
            start_temperature=start_temperature,
            end_temperature=end_temperature,
        )
        task_executor = None
        if n_jobs != 1:
            task_executor = TaskManager(n_jobs, mp_backend)
            if task_executor.n_jobs == 1:
                task_executor = None  # '1x per-gpu' on single GPU machines -> No need for multiprocess overhead

        callbacks = convert_to_list(callbacks)
        if keep_history:
            callbacks.append(self._history)
        if checkpoint_path is not None:
            checkpoint_path = convert_to_checkpoint_path(checkpoint_path)
            self._checkpoint_path = checkpoint_path
            callbacks.append(CheckpointCallback(checkpoint_path))

        self._pruner = pruner
        self._run_context = RunContext(
            direction,
            pruner,
            ignore_nans,
            schedule,
            callbacks,
            task_executor,
            quiet,
        )

        self._f_cache.set_enable(enable_rejection_cache)
        self._signal_listener.register_signal(
            schedule.signal_gradually_quit, self._force_termination
        )

        # The last step of the initialization is to potentially restore from a previous checkpoint
        if (
            checkpoint_path is not None
            and os.path.isfile(checkpoint_path)
            and not overwrite_checkpoint
        ):
            self.load(checkpoint_path)

        # Initialization for run is now done -> let's start search
        for c in self._run_context.callbacks:
            c.on_search_start(self)

        if self._best_f is None and self.manual_queue_count == 0:
            # Evaluate initial guess, this gives the user some estimate of how much PyHopper could tune the parameters
            # self._fill_missing_init_values()
            self._submit_candidate(
                objective_function,
                CandidateType.INIT,
                self._best_solution,
                kwargs,
            )
            schedule.increment_step()

        current_temperature = schedule.temperature
        # Before entering the loop, let's wait until we can run at least one candidate
        self._wait_for_one_free_executor()
        while not schedule.is_timeout(
            self._run_context.run_history.estimated_candidate_runtime
        ):
            if self.free_param_count == 0 and self.manual_queue_count == 0:
                raise ValueError(
                    "There are not parameters to tune (search space does not contain any `pyhopper.Parameter` instance)"
                )
            # If estimated runtime exceeds timeout let's already terminate
            if self.manual_queue_count > 0:
                candidate = self._manually_queued_candidates.pop(0)
                candidate_type = CandidateType.MANUALLY_ADDED
            elif schedule.is_in_seeding_mode():
                candidate = self.sample_solution()
                candidate_type = CandidateType.RANDOM_SEEDING
            else:
                candidate = self.mutate_from_best(temperature=current_temperature)
                candidate_type = CandidateType.LOCAL_SAMPLING
            if candidate not in self._f_cache:
                # If candidate was already run before, let's skip this step
                self._submit_candidate(
                    objective_function,
                    candidate_type,
                    candidate,
                    kwargs,
                )
                current_temperature = schedule.temperature
            else:
                # Reject sample
                current_temperature *= (
                    1.05  # increase temperature by 5% if we found a duplicate
                )
                current_temperature = max(current_temperature, 1)
            schedule.increment_step()
            # Before entering the loop, let's wait until we can run at least one candidate
            self._wait_for_one_free_executor()

        self._run_context.terminate = True
        self._wait_for_all_running_jobs()

        for c in self._run_context.callbacks:
            c.on_search_end()
        self._signal_listener.unregister_signal()

        # Clean up the run context (task executor,progbar,run history)
        del self._run_context
        self._run_context = None

        return self._best_solution

    def save(self, checkpoint_path, pruner=None) -> str:
        """Saves the internal state of the hyperparameter search (history, current best, etc.) at the given checkpoint path."""
        state_dict = {}

        if self._run_context is not None and self._run_context.pruner is not None:
            if pruner is not None and pruner != self._run_context.pruner:
                raise ValueError(
                    f"Error. Pruner object passed to 'save' and other pruner object passed to 'run'"
                )
            pruner = self._run_context.pruner

        # Don't save run_context if .terminate is True
        state_dict["run_context"] = (
            None
            if self._run_context is None or self._run_context.terminate
            else self._run_context.state_dict()
        )
        state_dict["cache"] = self._f_cache.state_dict()
        state_dict["best_f"] = self._best_f
        state_dict["history"] = self._history.state_dict()

        if pruner is not None:
            state_dict["pruner"] = pruner.state_dict()
        state_dict["best_solution"] = self._best_solution

        checkpoint_path = convert_to_checkpoint_path(checkpoint_path)
        store_dict(checkpoint_path, state_dict)
        return checkpoint_path

    def load(self, checkpoint_path, pruner=None):
        """Loads the internal state of the hyperparameter search (history, current best, etc.) at the given checkpoint path.

        :param checkpoint_path: File from which to load the checkpoint
        :param pruner: Pruner object whose internal state should also be loaded from the checkpoint
        """
        state_dict = load_dict(checkpoint_path)

        try:
            if state_dict["run_context"] is not None:
                self._run_context.load_state_dict(state_dict["run_context"])

            self._f_cache.load_state_dict(state_dict["cache"])
            self._history.load_state_dict(state_dict["history"])
            self._best_f = state_dict["best_f"]
            self._best_solution = state_dict["best_solution"]
            if "pruner" in state_dict:
                if (
                    self._run_context is not None
                    and self._run_context.pruner is not None
                ):
                    if pruner is not None and pruner != self._run_context.pruner:
                        raise ValueError(
                            f"Error. Pruner object passed to 'load' and other pruner object passed to 'run'"
                        )
                    pruner = self._run_context.pruner
                if pruner is not None:
                    pruner.load_state_dict(state_dict["pruner"])

        except KeyError as e:
            raise ValueError(f"Could not parse file '{checkpoint_path}' ({str(e)})")

    @property
    def manual_queue_count(self) -> int:
        """Number of candidate parameters that are manually added by the user and will be evaluated first when run is called"""
        return len(self._manually_queued_candidates)

    @property
    def free_param_count(self) -> int:
        """Number of free (optimizable) parameters"""
        return self._free_param_count

    @property
    def checkpoint_path(self) -> Optional[str]:
        """Path to the checkpoint file in which the intermediate state of the search will be stored.
        Equals the `checkpoint_path` argument of `search.run()` if the argument was a file.
        If the `checkpoint_path` argument of `search.run()` was a directory, then the newly created file checkpoint file will be returned.
        None if `search.run` was called without providing a `checkpoint_path`"""
        return self._checkpoint_path

    @property
    def best(self) -> Optional[dict]:
        """A dict object containing the best found parameter so far. None if no candidate has been evaluated yet."""
        return self._best_solution

    @property
    def best_f(self) -> Optional[float]:
        """The objective value of the best found parameter so far. None if no candidate has been evaluated yet."""
        return self._best_f

    @property
    def history(self) -> History:
        """Contains a list of all evaluated candidates and corresponding objective values so far."""
        return self._history