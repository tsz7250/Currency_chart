"""固定兩階段方向與共用情境區間；不依幣對選參數。"""
import math
import numpy as np
from .forecast_supervised import weights


def binary_fit(rows,labels,balanced=False):
    from sklearn.pipeline import make_pipeline
    from sklearn.impute import SimpleImputer
    from sklearn.preprocessing import StandardScaler
    from sklearn.linear_model import LogisticRegression
    if len(set(labels))<2:return float(labels[0]) if labels else .5
    model=make_pipeline(SimpleImputer(strategy='median',keep_empty_features=True,add_indicator=True),StandardScaler(),
        LogisticRegression(C=1,max_iter=2000,random_state=0,class_weight='balanced' if balanced else None))
    model.fit([r['x'] for r in rows],labels,logisticregression__sample_weight=weights(rows))
    return model


def train_direction(rows):
    gate=binary_fit(rows,[int(r['label']==1) for r in rows],True)
    nonflat=[r for r in rows if r['label']!=1]
    sign=binary_fit(nonflat,[int(r['label']==2) for r in nonflat])
    return gate,sign


def direction(model,rows):
    def predict(m):return np.full(len(rows),m) if isinstance(m,float) else m.predict_proba([r['x'] for r in rows])[:,list(m.classes_).index(1)]
    flat,up=predict(model[0]),predict(model[1])
    return np.column_stack(((1-flat)*(1-up),flat,(1-flat)*up))


def sigma(row):
    value=row['x'][4]
    return max(value if math.isfinite(value) else 0,1e-5)*math.sqrt(row['horizon'])


def weighted_quantile(values,weights,p):
    order=np.argsort(values);values=np.asarray(values)[order];w=np.asarray(weights)[order]
    return float(values[min(np.searchsorted(np.cumsum(w),p*w.sum()),len(values)-1)])


def bounds(rows,medians,calibration,cal_medians,grouped=False):
    output=[];audit=[]
    for row,median in zip(rows,medians):
        indices=[i for i,r in enumerate(calibration) if r['horizon']==row['horizon']]
        if len(indices)<40 or len({calibration[i]['origin'] for i in indices})<20:
            raise ValueError('共用校準樣本不足，不刪除測試起點')
        values=np.array([math.log(calibration[i]['actual']/cal_medians[i])/sigma(calibration[i]) for i in indices])
        group=median>=row['base'];mask=np.array([(cal_medians[i]>=calibration[i]['base'])==group for i in indices])
        n=int(mask.sum());dates=len({calibration[i]['origin'] for i,m in zip(indices,mask) if m})
        lam=n/(n+60) if grouped and n>=40 and dates>=20 else 0
        w=np.full(len(indices),(1-lam)/len(indices))
        if lam:w[mask]+=lam/n
        lower=weighted_quantile(values,w,.1);upper=weighted_quantile(values,w,.9)
        lo,hi=median*math.exp(lower*sigma(row)),median*math.exp(upper*sigma(row))
        clamped=(min(lo,median),max(hi,median))
        output.append([clamped[0],median,clamped[1]])
        audit.append(dict(group='nonnegative' if group else 'negative',samples=len(indices),group_samples=n,group_dates=dates,
            group_weight=lam,fallback=bool(grouped and not lam),constraint_added_width=(clamped[1]-clamped[0])-(hi-lo)))
    return np.array(output),audit
