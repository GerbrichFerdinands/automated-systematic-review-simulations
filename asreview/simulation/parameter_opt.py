import os
import tempfile
from multiprocessing import Process
import random
import logging
import copy
from distutils.dir_util import copy_tree

from modAL.models import ActiveLearner
import numpy as np
from tqdm import tqdm

from hyperopt import fmin, tpe, STATUS_OK, Trials
from asreview.review.factory import get_reviewer
from asreview.simulation.analysis import Analysis
from asreview.models.sklearn_models import SVCModel
import pickle
from asreview.readers import ASReviewData
from numpy import average
from asreview.balance_strategies.utils import get_balance_class
from asreview.models.utils import get_model_class

# logging.getLogger().setLevel(logging.DEBUG)

SVM_KERNELS = ['poly', 'rbf', 'sigmoid', 'linear']
BALANCE_STRATS = ['simple', 'undersample', 'triple_balance']
SVM_GAMMA = ['scale', 'auto']


def loss_spread(time_results, n_papers, moment=1.0):
    loss = 0
    for label in time_results:
        loss += (time_results[label]/n_papers)**moment
    return (loss**(1/moment))/len(time_results)


def loss_integrated(inc_results):
    inc_data = inc_results["data"]
    loss = 0
    x_reviewed = inc_data[0]
    y_reviewed = inc_data[1]
    dx = (inc_data[0][1] - inc_data[0][0])/100
    for y_val in y_reviewed:
        loss -= dx*y_val/100

    dx = (1-x_reviewed[-1]/100)
    dy = (1-y_reviewed[-1]/100)
    dy_avg = 1-dy/2
    loss -= dx * dy_avg
    return loss


def loss_WSS(inc_results):
    keys = []
    for key in inc_results:
        if key.startswith("WSS"):
            keys.append(key)

    loss = 0
    for key in keys:
        loss += loss_single_WSS(inc_results, key)/len(keys)
    return loss


def loss_single_WSS(inc_results, WSS_measure):
    inc_data = inc_results["data"]
    WSS_y = int(WSS_measure[3:])/100
    WSS_x = inc_results[WSS_measure]
    if WSS_x is not None:
        return WSS_x[1]
    last_x = inc_data[0][-1]
    last_y = inc_data[1][-1]
    b = (1-last_y)/(1-last_x)
    a = 1 - b
    if WSS_y > 0.99999:
        WSS_y = 0.99999
    WSS_x = (WSS_y - a)/b
    return WSS_x


def run_model(*args, model_name, balance_strategy, pid=0, model_kwargs={},
              balance_kwargs={}, **kwargs):
    reviewer = get_reviewer(*args, model=model_name,
                            balance_strategy=balance_strategy, **kwargs)
    rand_seed = pid

    np.random.seed(rand_seed)
    random.seed(rand_seed)
    model_class = get_model_class(model_name)
    reviewer.model = model_class(
        model_kwargs=model_kwargs, random_state=rand_seed).model()
    reviewer.balance_strategy, reviewer.balance_kwargs = get_balance_class(
        method=balance_strategy)(
        balance_kwargs, fit_kwargs=reviewer.fit_kwargs,
        query_kwargs=reviewer.query_kwargs).func_kwargs()
    logging.debug(f"Balance kwargs: {reviewer.balance_kwargs}")
    reviewer.learner = ActiveLearner(
            estimator=reviewer.model,
            query_strategy=reviewer.query_strategy
    )
    reviewer.review()


def loss_from_dataset(dataname, dataset, model, balance_strategy, params,
                      query_strategy, n_instances, n_papers,
                      n_runs, included_sets, excluded_sets,
                      **kwargs):
    log_dir = os.path.join("temp", dataname)
    os.makedirs(log_dir, exist_ok=True)

    logging.debug(f"params 2: {params}")
    params = copy.deepcopy(params)
    run_args = [dataset, "simulate"]
    run_kwargs = dict(
        query_strategy=query_strategy,
        n_instances=n_instances,
        n_papers=n_papers,
        **kwargs
    )

    logging.debug(f"params 3: {params}")
    model_kwargs = {}
    balance_kwargs = {}
    for par in params:
        if par.startswith("mdl_"):
            model_kwargs[par[4:]] = params[par]
        elif par.startswith("bal_"):
            balance_kwargs[par[4:]] = params[par]
        else:
            run_kwargs[par] = params

    logging.debug(f"Balance 2: {balance_kwargs}")
    run_kwargs["model_name"] = model
    run_kwargs["balance_strategy"] = balance_strategy
    run_kwargs["model_kwargs"] = model_kwargs
    run_kwargs["balance_kwargs"] = balance_kwargs
    procs = []
    for i_run in range(n_runs):
        run_kwargs["log_file"] = os.path.join(
            log_dir, f"results_{i_run}.json")
        run_kwargs["prior_included"] = included_sets[i_run]
        run_kwargs["prior_excluded"] = excluded_sets[i_run]
        run_kwargs["pid"] = i_run
        p = Process(
            target=run_model,
            args=copy.deepcopy(run_args),
            kwargs=copy.deepcopy(run_kwargs),
            daemon=True,
        )
        procs.append(p)

    for p in procs:
        p.start()

    for p in procs:
        p.join()

    analysis = Analysis.from_dir(log_dir)
    results = analysis.avg_time_to_discovery()
    loss = loss_spread(results, len(analysis.labels), 1.0)

    return loss


def create_objective_func(datasets,
                          model,
                          balance_strategy,
                          query_strategy="rand_max",
                          n_runs=8, n_included=10, n_excluded=10, n_papers=520,
                          n_instances=50, **kwargs):

    files = {}
    excluded_sets = {}
    included_sets = {}
    for dataset in datasets:
        files[dataset] = os.path.join("..", "..", "data", "test", dataset+".csv")
        asdata = ASReviewData.from_file(files[dataset])
        ones = np.where(asdata.labels == 1)[0]
        zeros = np.where(asdata.labels == 0)[0]

        np.random.seed(81276149)

        included_sets[dataset] = []
        excluded_sets[dataset] = []
        for _ in range(n_runs):
            included_sets[dataset].append(
                np.random.choice(ones, n_included, replace=False))
            excluded_sets[dataset].append(
                np.random.choice(zeros, n_excluded, replace=False))

    def objective_func(params):
        loss = []
        logging.debug(params)
        for dataset in datasets:
            loss.append(
                loss_from_dataset(
                    dataset, files[dataset], model, balance_strategy, params,
                    query_strategy, n_instances,
                    n_papers, n_runs, included_sets[dataset],
                    excluded_sets[dataset],
                    **kwargs)
            )
        return {"loss": average(loss), 'status': STATUS_OK}
    return objective_func


def hyper_optimize(datasets=["ptsd", "ace", "hall"],
                   model="svm",
                   balance_strategy="simple",
                   n_iter=20,
                   trials_fp=None):
    obj_fun = create_objective_func(datasets, model, balance_strategy)

    model = get_model_class(model)
    balance = get_balance_class(balance_strategy)
    hyper_space, hyper_names = model().hyper_space()
    hyper_space.update(balance().hyperopt_space())

    trials = None
    if trials_fp is not None:
        try:
            with open(trials_fp, "rb") as fp:
                trials = pickle.load(fp)
        except FileNotFoundError:
            print(f"Cannot find {trials_fp}")

    if trials is None:
        trials = Trials()
        n_start_evals = 0
    else:
        n_start_evals = len(trials.trials)

    for i in tqdm(range(n_iter)):
        fmin(fn=obj_fun,
             space=hyper_space,
             algo=tpe.suggest,
             max_evals=i+n_start_evals+1,
             trials=trials,
             show_progressbar=False)
        with open(trials_fp, "wb") as fp:
            pickle.dump(trials, fp)
        if trials.best_trial['tid'] == len(trials.trials)-1:
            copy_tree("temp", "best")

    return trials, hyper_names
