"""依日期及標籤成熟時間切分；不依資料列位置推定時間。"""
from datetime import timedelta

CORE=('TWD-HKD','USD-TWD','JPY-TWD','EUR-USD')
HORIZONS=(7,14,30)
VERSION='nested-calendar-v1'


def folds(start,end):
    first=end-timedelta(days=119)
    if first-timedelta(days=120)<=start+timedelta(days=60):
        raise ValueError('資料不足以建立巢狀時間回測')
    result=[]
    for i in range(3):
        test=first+timedelta(days=30*i);cal=test-timedelta(days=60)
        result.append(dict(fold=i+1,calibration=cal,test=test,end=test+timedelta(days=30),
            inner=[dict(start=cal-timedelta(days=a),end=cal-timedelta(days=b)) for a,b in ((60,45),(45,30))]))
    return result


def select(rows,fold,role,inner=None):
    if role=='inner_train':
        cutoff=fold['inner'][inner]['start']
        return [r for r in rows if r['pair'] in CORE and r['target']<cutoff]
    if role=='inner_validation':
        block=fold['inner'][inner]
        return [r for r in rows if r['pair'] in CORE and block['start']<=r['origin']<block['end']
                and r['target']<fold['calibration']]
    if role=='train':
        return [r for r in rows if r['pair'] in CORE and r['target']<fold['calibration']]
    if role=='calibration':
        return [r for r in rows if r['pair'] in CORE and fold['calibration']<=r['origin']
                and r['target']<fold['test']]
    if role=='test':
        return [r for r in rows if fold['test']<=r['origin']<fold['end']]
    raise ValueError('未知切分角色')


def calibration_counts(rows,h):
    group=[r for r in rows if r['horizon']==h]
    return dict(samples=len(group),dates=len({r['origin'] for r in group}))


def validate_test(rows,fold,pairs,horizons=range(1,31)):
    expected={(p,fold['test']+timedelta(days=i),h) for p in pairs for i in range(30) for h in horizons}
    keys=[(r['pair'],r['origin'],r['horizon']) for r in rows]
    if len(keys)!=len(set(keys)) or set(keys)!=expected:
        raise ValueError('共同測試起點缺漏或重複，不可刪除樣本')


def stage_summary(rows,cutoff):
    if not rows:raise ValueError('切分階段沒有樣本')
    if cutoff is not None and any(r['target']>=cutoff for r in rows):raise ValueError('標籤跨越截止時間')
    return dict(samples=len(rows),origins=len({r['origin'] for r in rows}),
        latest_target=max(r['target'] for r in rows),cutoff=cutoff,
        per_horizon={str(h):calibration_counts(rows,h) for h in HORIZONS})
