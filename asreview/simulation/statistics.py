
import numpy as np


def _find_inclusions(logger, labels, remove_initial=True):
    inclusions = []
    n_initial_inc = 0
    cur_inclusions = 0
    n_initial = 0
    n_queries = logger.n_queries()
    for query_i in range(n_queries):
        label_methods = logger.get("label_methods", query_i)
        label_idx = logger.get("label_idx", query_i)
        for i in range(len(label_idx)):
            if label_methods[i] == "initial" and remove_initial:
                n_initial_inc += labels[label_idx[i]]
                n_initial += 1
            else:
                cur_inclusions += labels[label_idx[i]]
                inclusions.append(cur_inclusions)

    inclusions_after_init = sum(labels)
    if remove_initial:
        inclusions_after_init -= n_initial_inc
    return inclusions, inclusions_after_init, n_initial


def _get_labeled_order(logger):
    label_order = []
    n_initial = 0
    n_queries = logger.n_queries()
    for query_i in range(n_queries):
        label_methods = logger.get("label_methods", query_i)
        label_idx = logger.get("label_idx", query_i)
        for i in range(len(label_idx)):
            if label_methods[i] == "initial":
                n_initial += 1
        label_order.extend(label_idx)
    return label_order, n_initial


def _get_last_proba_order(logger):

    n_queries = logger.n_queries()
    pool_idx = None
    for query_i in reversed(range(n_queries)):
        pool_idx = logger.get("pool_idx", query_i)
        if pool_idx is not None:
            proba = logger.get("proba", query_i)
            break

    if pool_idx is None:
        return []
    return pool_idx[np.argsort(-proba[pool_idx])]


def _get_proba_order(logger, query_i):
    try:
        pool_idx = logger.get("pool_idx", query_i)
    except KeyError:
        pool_idx = None

    if pool_idx is None:
        return None
    proba = logger.get("proba", query_i)[pool_idx]
    return pool_idx[np.argsort(proba)]


def _n_false_neg(logger, query_i, labels):
    proba_order = _get_proba_order(logger, query_i)
    if proba_order is None:
        return None
    res = np.zeros(len(proba_order))

    n_one = 0
    for i in range(len(res)):
        if labels[proba_order[i]] == 1:
            n_one += 1
        res[i] = n_one
    return np.array(list(reversed(res)))


def _get_limits(loggers, query_i, labels, proba_allow_miss=[]):
    num_left = None

    for logger in loggers.values():
        new_num_left = _n_false_neg(logger, query_i, labels)
        if new_num_left is None:
            return None

        if num_left is None:
            num_left = new_num_left
        else:
            num_left += new_num_left
    num_left /= len(loggers)
    limits = [len(num_left)]*len(proba_allow_miss)
    allow_miss = {i: proba for i, proba in enumerate(proba_allow_miss)}
#     allow_miss = list(zip(proba_allow_miss, range(len(proba_allow_miss)))
    for i in range(len(num_left)):
        for i_prob, prob in list(allow_miss.items()):
            if num_left[i] < prob:
                limits[i_prob] = i
                del allow_miss[i_prob]
        if len(allow_miss) == 0:
            break
    return limits
