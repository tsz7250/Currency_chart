"""發布端固定日曆回放與統一評分。"""
import math
from datetime import date,timedelta
from .forecast import context,measurement,score,correction,widen,summary,classification,MODEL_ID,MODEL_REVISION


def replay(entries,predict):
    lookup=dict(entries);latest=entries[-1][0]
    origins=[d for d,_ in entries if context([(t,v) for t,v in entries if t<=d]) is not None]
    batch=[context([(t,v) for t,v in entries if t<=d]) for d in origins]
    forecasts=predict(batch)
    if len(forecasts)!=len(origins):raise ValueError('預測批次不一致')
    known={h:[] for h in range(1,31)};rows={h:{k:[] for k in ('raw','calibrated','flat','raw_matched')} for h in range(1,31)};points=[]
    for origin,forecast in zip(origins,forecasts):
        if len(forecast)!=30:raise ValueError('預測長度錯誤')
        for h,q in enumerate(forecast,1):
            lo,median,hi=map(float,q)
            if not all(math.isfinite(v) and v>0 for v in q) or not lo<=median<=hi:raise ValueError('無效預測')
            target=origin+timedelta(days=h)
            delta,n=correction(known[h],origin);bounds=widen(lo,hi,delta) if delta is not None else (None,None)
            if origin==latest:points.append(dict(date=str(target),median=median,low80=bounds[0],high80=bounds[1],calibration_samples=n))
            actual=lookup.get(target)
            if actual is None:continue
            if origin>=latest-timedelta(days=179):
                rows[h]['raw'].append(measurement(origin,target,actual,lo,median,hi))
                rows[h]['flat'].append(measurement(origin,target,actual,lookup[origin],lookup[origin],lookup[origin]))
                if delta is not None:
                    rows[h]['calibrated'].append(measurement(origin,target,actual,bounds[0],median,bounds[1]))
                    rows[h]['raw_matched'].append(rows[h]['raw'][-1])
            known[h].append(dict(target=target,score=score(lo,hi,actual)))
    report=dict(model=MODEL_ID,model_revision=MODEL_REVISION,algorithm_version='stable-full-history-v1',
        as_of=str(latest),training_start=str(entries[0][0]),context_days=90,points=points,
        recent_history=[dict(date=str(latest-timedelta(days=i)),rate=lookup.get(latest-timedelta(days=i))) for i in range(6,-1,-1)],
        backtest={str(h):dict(**{k:summary(v) for k,v in groups.items()},classification=classification(groups['raw'],lookup),
                           score_source='historical_replay') for h,groups in rows.items()})
    return report,rows


def realized(records,archive,pair,h):
    """正式結果只使用真正發出且已成熟的預測。"""
    from .alerts import now_taipei
    lookup={date.fromisoformat(d):v['rate'] for d,v in archive.history(pair).items()};rows=[];seen=set()
    for payload in records:
        report=payload['forecasts'][pair];point=report['points'][h-1];target=date.fromisoformat(point['date'])
        if report['as_of'] in seen:continue
        seen.add(report['as_of'])
        if target>now_taipei().date() or target not in lookup or point['low80'] is None:continue
        rows.append(measurement(date.fromisoformat(report['as_of']),target,lookup[target],point['low80'],point['median'],point['high80']))
    return dict(**summary(rows),classification=classification(rows,lookup),score_source='issued_forecasts')
