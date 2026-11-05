"""新版評分與判讀；不改寫既有評測或影子期的語意。"""
import math
import numpy as np
from .forecast_supervised import PAIRS,HORIZONS

VERSION='evaluation-v2'
RULES=dict(replacement_engineering_v1=dict(price_gain=.05,direction_f1_gain=.03,accuracy_loss=.02,
    coverage=[.75,.85],coverage_floor=.7,score_gain=.1,width_increase=.2),
    interval_dual_goal_v1=dict(coverage=[.78,.82],coverage_floor=.7,score_gain=.1,width_gain=.05))


def classification(matrix):
    support=matrix.sum(axis=1);predicted=matrix.sum(axis=0);tp=np.diag(matrix)
    precision=np.divide(tp,predicted,out=np.zeros(3),where=predicted>0)
    recall=np.divide(tp,support,out=np.zeros(3),where=support>0)
    f1=np.divide(2*tp,support+predicted,out=np.zeros(3),where=support+predicted>0)
    active=support+predicted>0;n=matrix.sum()
    return dict(samples=int(n),accuracy=float(tp.sum()/n) if n else None,
        precision=float(precision.mean()) if n else None,recall=float(recall.mean()) if n else None,
        f1=float(f1.mean()) if n else None,active_label_f1=float(f1[active].mean()) if n else None,
        confusion_matrix=matrix.astype(int).tolist(),labels=['down','flat','up'],threshold=.001,
        per_class={name:dict(support=int(support[i]),predicted=int(predicted[i]),precision=float(precision[i]),
            recall=float(recall[i]),f1=float(f1[i])) for i,name in enumerate(('down','flat','up'))})


def groups(cells):
    result={}
    for name in ('core','extra','all'):
        selected=[v for k,v in cells.items() if name=='all' or k.startswith(('AUD-','GBP-'))==(name=='extra')]
        fields=('accuracy','precision','recall','f1','active_label_f1','log_loss') if 'accuracy' in selected[0] else (
            'normalized_mae','coverage80','normalized_width','normalized_interval_score','lower_miss_rate','upper_miss_rate')
        result[name]={f:float(np.mean([v[f] for v in selected])) if all(v.get(f) is not None for v in selected) else None for f in fields}
    return result


class Evaluation:
    def __init__(self,rows):
        self.rows=rows;self.actual=np.array([r['actual'] for r in rows]);self.base=np.array([r['base'] for r in rows])
        self.labels=np.array([r['label'] for r in rows],dtype=int)
        self.dates=sorted({r['origin'] for r in rows});self.date_index=np.array([self.dates.index(r['origin']) for r in rows])
        self.cells={f'{p}/{h}':np.array([i for i,r in enumerate(rows) if r['pair']==p and r['horizon']==h]) for p in PAIRS for h in HORIZONS}
        if any(len(i)==0 for i in self.cells.values()):raise ValueError('缺少共同幣對／期間')

    def direction(self,p,weights=None,probabilistic=True):
        p=np.asarray(p,dtype=float)
        if p.shape!=(len(self.rows),3) or not np.isfinite(p).all() or (p<0).any() or not np.allclose(p.sum(axis=1),1):raise ValueError('無效機率')
        w=np.ones(len(self.rows)) if weights is None else weights;pred=p.argmax(axis=1);result={}
        for key,i in self.cells.items():
            matrix=np.bincount(self.labels[i]*3+pred[i],weights=w[i],minlength=9).reshape(3,3)
            m=classification(matrix)
            m['log_loss']=float(np.average(-np.log(np.maximum(p[i,self.labels[i]],1e-15)),weights=w[i])) if probabilistic and w[i].sum() else None
            result[key]=m
        return result

    def price(self,q,weights=None):
        q=np.asarray(q,dtype=float)
        if q.shape!=(len(self.rows),3) or not np.isfinite(q[:,1]).all() or (q[:,1]<=0).any():raise ValueError('無效中心價格')
        valid=np.isfinite(q[:,0]) & np.isfinite(q[:,2])
        if np.any(np.isfinite(q[:,0])!=np.isfinite(q[:,2])):raise ValueError('不完整區間')
        if np.any(valid & ((q[:,0]<=0)|(q[:,0]>q[:,1])|(q[:,1]>q[:,2]))):raise ValueError('無效上下界')
        w=np.ones(len(self.rows)) if weights is None else weights;result={}
        for key,i in self.cells.items():
            total=w[i].sum();actual=self.actual[i];error=abs(q[i,1]-actual)
            m=dict(samples=int(total),mae=float(np.average(error,weights=w[i])) if total else None,
                normalized_mae=float(np.sum(error*w[i])/np.sum(actual*w[i])) if total else None)
            j=i[valid[i]];n=w[j].sum()
            m.update(interval_samples=int(n),coverage80=None,mean_width=None,interval_score80=None,
                normalized_width=None,normalized_interval_score=None,lower_miss_rate=None,upper_miss_rate=None)
            if n:
                lo,hi=q[j,0],q[j,2];a=self.actual[j];width=hi-lo
                score=width+10*np.maximum(np.maximum(lo-a,a-hi),0)
                m.update(coverage80=float(np.average((a>=lo)&(a<=hi),weights=w[j])),
                    mean_width=float(np.average(width,weights=w[j])),interval_score80=float(np.average(score,weights=w[j])),
                    normalized_width=float(np.sum(width*w[j])/np.sum(a*w[j])),
                    normalized_interval_score=float(np.sum(score*w[j])/np.sum(a*w[j])),
                    lower_miss_rate=float(np.average(a<lo,weights=w[j])),upper_miss_rate=float(np.average(a>hi,weights=w[j])))
            result[key]=m
        return result


def common_intervals(prices):
    values={n:np.asarray(q,dtype=float).copy() for n,q in prices.items()}
    shapes={q.shape for q in values.values()}
    if len(shapes)!=1 or any(q.ndim!=2 or q.shape[1]!=3 for q in values.values()):raise ValueError('比較樣本形狀不一致')
    if any(np.isinf(q).any() or np.any(np.isnan(q[:,0])!=np.isnan(q[:,2])) for q in values.values()):raise ValueError('區間無效，不能當成一般缺值排除')
    valid=np.logical_and.reduce([np.isfinite(q[:,0]) & np.isfinite(q[:,2]) for q in values.values()])
    counts={n:dict(available=int((np.isfinite(q[:,0])&np.isfinite(q[:,2])).sum()),common=int(valid.sum()),
        excluded=int(len(q)-valid.sum())) for n,q in values.items()}
    for q in values.values():q[~valid,0]=np.nan;q[~valid,2]=np.nan
    return values,counts


def circular_counts(n,length,rng):
    starts=rng.integers(0,n,size=math.ceil(n/length))
    indices=np.concatenate([(s+np.arange(length))%n for s in starts])[:n]
    return np.bincount(indices,minlength=n)


def uncertainty(evaluation,directions,prices,reference='bolt',repetitions=2000):
    """每次候選、基準、全部幣對及期間共用日期抽樣；不重新擬合模型。"""
    output={}
    for length in (30,7,14):
        rng=np.random.default_rng(0);estimates={}
        def add(key,value):
            if value is not None and np.isfinite(value):estimates.setdefault(key,[]).append(float(value))
        for _ in range(repetitions):
            counts=circular_counts(len(evaluation.dates),length,rng);w=counts[evaluation.date_index]
            dr={n:groups(evaluation.direction(p,w,False)) for n,p in directions.items()}
            pr={n:groups(evaluation.price(q,w)) for n,q in prices.items()}
            for name,c in dr.items():
                for group in ('core','extra','all'):
                    add(f'direction/{name}/{group}/f1',c[group]['f1'])
                    if reference in dr and name!=reference:add(f'direction/{name}-minus-{reference}/{group}/f1',c[group]['f1']-dr[reference][group]['f1'])
            for name,c in pr.items():
                for group in ('core','extra','all'):
                    for field in ('normalized_mae','coverage80','normalized_width','normalized_interval_score'):
                        add(f'price/{name}/{group}/{field}',c[group][field])
                        if reference in pr and name!=reference and c[group][field] is not None and pr[reference][group][field] is not None:
                            add(f'price/{name}-minus-{reference}/{group}/{field}',c[group][field]-pr[reference][group][field])
                    if reference in pr and name!=reference and pr[reference][group]['normalized_mae']:
                        add(f'price/{name}/{group}/mae_relative_improvement',1-c[group]['normalized_mae']/pr[reference][group]['normalized_mae'])
        output[str(length)]=dict(repetitions=repetitions,intervals95={k:np.quantile(v,[.025,.975]).tolist() for k,v in estimates.items()},
            interpretation='exploratory sensitivity only; overlapping targets and only 90 origins; no precise coverage guarantee')
    return output


def interval_checks(candidate,baseline):
    a,b=groups(candidate)['all'],groups(baseline)['all']
    complete=all(v['interval_samples']>=60 for c in (candidate,baseline) for v in c.values())
    if not complete:return dict(complete=False),dict(complete=False)
    floor=all(v['coverage80']>=.7 for v in candidate.values())
    score=a['normalized_interval_score']<=.9*b['normalized_interval_score']
    return dict(complete=True,coverage_target=.75<=a['coverage80']<=.85,coverage_floor=floor,
        interval_score_10_percent=score,width_limit=a['normalized_width']<=1.2*b['normalized_width']),dict(
        complete=True,coverage_target=.78<=a['coverage80']<=.82,coverage_floor=floor,
        interval_score_10_percent=score,width_5_percent=a['normalized_width']<=.95*b['normalized_width'])


def worth_direction(candidate,baseline,fold_pairs):
    a,b=groups(candidate)['all'],groups(baseline)['all']
    positive=sum(groups(c)['all']['f1']>groups(r)['all']['f1'] for c,r in fold_pairs)
    return dict(f1_improved=a['f1']>b['f1'],accuracy_guard=a['accuracy']>=b['accuracy']-.02,folds_improved=positive,
        worth_further_testing=a['f1']>b['f1'] and a['accuracy']>=b['accuracy']-.02 and positive>=2)


def worth_interval(candidate,baseline,fold_pairs):
    a,b=groups(candidate)['all'],groups(baseline)['all']
    if any(c[f] is None for c in (a,b) for f in ('coverage80','normalized_width','normalized_interval_score')):
        return dict(worth_further_testing=False,reason='insufficient_common_intervals')
    positive=sum(groups(c)['all']['normalized_interval_score']<groups(r)['all']['normalized_interval_score'] for c,r in fold_pairs)
    quality=a['normalized_interval_score']<b['normalized_interval_score']
    useful=abs(a['coverage80']-.8)<abs(b['coverage80']-.8) or (a['normalized_width']<b['normalized_width'] and a['coverage80']>=b['coverage80']-.02)
    return dict(score_improved=quality,folds_improved=positive,coverage_or_width_improved=useful,
        worth_further_testing=quality and positive>=2 and useful,
        below_70_cells=[k for k,v in candidate.items() if v['coverage80']<.7])


def price_checks(candidate,baseline,flat):
    engineering,dual=interval_checks(candidate,baseline)
    a,b,f=groups(candidate),groups(baseline),groups(flat)
    engineering.update(price_5_percent=a['core']['normalized_mae']<=.95*b['core']['normalized_mae'],
        better_than_flat=a['core']['normalized_mae']<f['core']['normalized_mae'],
        no_cell_10_percent_worse=all(candidate[k]['normalized_mae']<=baseline[k]['normalized_mae']*1.1 for k in candidate if not k.startswith(('AUD-','GBP-'))),
        extra_pairs_no_worse=a['extra']['normalized_mae']<=b['extra']['normalized_mae'])
    return engineering,dual
