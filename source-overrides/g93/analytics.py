import pandas as pd
import numpy as np

def built_unit_prices(frame,basis):
    if basis not in ('land_area','building_area'):raise ValueError('Unknown area basis')
    result=frame.copy()
    built=result.category.eq('Zeme ar ēkām')
    area=pd.to_numeric(result[basis],errors='coerce')
    if basis=='land_area' and 'without_land' in result:
        area=area.mask(result.without_land.fillna(False))
    eligible=built & result.valid & area.gt(0) & result.amount.gt(0)
    result.loc[built,'area']=area[built].where(area[built].gt(0))
    result.loc[built,'price_m2']=float('nan')
    result.loc[eligible,'price_m2']=result.loc[eligible,'amount']/area[eligible]
    result.loc[built,'price_basis']=basis
    return result

def mark_statistical_outliers(frame,by_village=False):
    """Compare unit prices within territory/category/year, with at least 10 prices."""
    result=frame.copy()
    result['exclude_price_stats']=False
    eligible=result.valid & result.price_m2.gt(0) & np.isfinite(result.price_m2) & result.area.gt(0)
    territory=pd.Series('',index=result.index,dtype='object')
    fields=['village_code'] if by_village else ['parish_code','city_code']
    for field in fields:
        codes=result[field].fillna('').astype(str).str.strip() if field in result else pd.Series('',index=result.index)
        use=territory.eq('') & codes.ne('')
        territory.loc[use]=field+':'+codes.loc[use]
    eligible &= territory.ne('')
    candidates=result.loc[eligible].assign(_outlier_territory=territory[eligible])
    for _,group in candidates.groupby(['category','year','_outlier_territory'],dropna=False):
        if len(group)<10:continue
        logs=np.log(group.price_m2)
        q1,q3=logs.quantile([0.25,0.75])
        spread=q3-q1
        if spread<=1e-12:continue
        outside=(logs<q1-1.5*spread)|(logs>q3+1.5*spread)
        result.loc[group.index[outside],'exclude_price_stats']=True
    return result

def metrics(frame):
    f=frame[frame.valid] if 'valid' in frame else frame
    monetary=f[~f.exclude_price_stats] if 'exclude_price_stats' in f else f
    prices=monetary[monetary.price_m2.notna() & monetary.area.gt(0)]
    result={'count':len(f),'sum':monetary.amount.sum(min_count=1),'mean_amount':monetary.amount.mean(),'median_amount':monetary.amount.median(),'area':f.area.sum(min_count=1),'area_n':int(f.area.notna().sum()),'price_n':len(prices),'mean_price':prices.price_m2.mean(),'median_price':prices.price_m2.median(),'weighted_price':prices.amount.sum()/prices.area.sum() if len(prices) else None}
    if frame.attrs.get('price_unit')=='EUR/ha':
        for key in ('mean_price','median_price','weighted_price'):
            if result[key] is not None:result[key]*=10000
    for column in ['land_area','building_area']:
        if column in f:result[column]=f[column].sum(min_count=1)
    return result

def aggregate(frame,columns):
    if frame.empty:return pd.DataFrame(columns=[*columns,'count','sum','area','median_price','weighted_price','price_n'])
    result=[]
    for keys,group in frame.groupby(columns,dropna=False,sort=True):
        if not isinstance(keys,tuple):keys=(keys,)
        result.append({**dict(zip(columns,keys)),**metrics(group)})
    return pd.DataFrame(result)

def filter_frame(frame,filters):
    result=frame
    for field,values in filters.items():
        if values:result=result[result[field].isin(values)]
    return result
