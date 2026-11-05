"""固定價格線的帶符號校準與殘差上下界。"""
import math
import numpy as np


def quantile(values, alpha):
    values=sorted(values)
    if len(values)<40:
        raise ValueError('校準至少需要 40 筆成熟結果')
    rank=math.ceil((len(values)+1)*(1-alpha))
    if rank>len(values):
        raise ValueError('校準分位數無有限解')
    return values[rank-1]


def signed_score(low, high, actual):
    return max(math.log(low/actual),math.log(actual/high))


def constrain(bounds, medians):
    result=np.asarray(bounds,dtype=float).copy()
    before=result.copy()
    result[:,0]=np.minimum(result[:,0],medians)
    result[:,1]=np.maximum(result[:,1],medians)
    if not np.isfinite(result).all() or (result<=0).any() or (result[:,0]>result[:,1]).any():
        raise ValueError('無效價格區間')
    return result,dict(count=int(np.any(before!=result,axis=1).sum()),
                       added_width=float(np.sum((result[:,1]-result[:,0])-(before[:,1]-before[:,0]))))


def residual_bounds(model,rows):
    q=np.asarray(model.predict([r['x'] for r in rows]))
    crossings=int((q[:,0]>q[:,1]).sum())
    q=np.sort(q,axis=1)
    bounds=np.exp(q)*np.array([r['median'] for r in rows])[:,None]
    return bounds,crossings
