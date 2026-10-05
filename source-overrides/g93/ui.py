import json
import math
from datetime import date
import duckdb,pandas as pd,plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from shapely.geometry import shape
import streamlit as st
from .config import ROOT,DATA,CONFIG
from .update import current
from .analytics import metrics,aggregate,built_unit_prices,mark_statistical_outliers
from .export import export_csv,export_excel
from .date_picker import latvian_date_input
from .land_use import land_use_name

CATEGORIES=['Zeme','Zeme ar ēkām','Dzīvokļi']
PAGES=CATEGORIES+['Teritoriju salīdzinājums','Metodika']
LABELS={'year':'Gads','district':'Novads / valstspilsēta','count':'Darījumu skaits','sum':'Summa, EUR','area':'Platība, m²','median_price':'Mediānas cena, EUR/m²','weighted_price':'Svērtā cena, EUR/m²','price_n':'Cenu izlases apjoms','area_n':'Platības izlases apjoms','mean_amount':'Vidējā summa, EUR','median_amount':'Mediānas summa, EUR','mean_price':'Vidējā cena, EUR/m²','count_change_pct':'Skaita izmaiņas, %','kind':'Avota veids','origin':'Avota paaudze','rows':'Avota rindas','unique_ids':'Unikāli ID','rule':'Kvalitātes pazīme','n':'Skaits','ids':'ID skaits','category':'Kategorija'}
LABELS.update(land_area='Zemes platība, m²',building_area='Būvju platība, m²')
def chart_number(value,decimals=0):
    if pd.notna(value) and value!=0 and round(value,decimals)==0:
        decimals=max(decimals,-math.floor(math.log10(abs(value))))
    return fmt(value,decimals)

def price_label(text):
    return text.replace('EUR/m²',st.session_state.get('_price_unit','EUR/m²'))

def price_decimals():return 0 if st.session_state.get('_price_unit')=='EUR/ha' else 2

def present(frame):
    result=frame.rename(columns={k:price_label(v) for k,v in LABELS.items()}).round(2)
    if price_decimals()==0:
        for column in [price_label('Mediānas cena, EUR/m²'),price_label('Svērtā cena, EUR/m²'),price_label('Vidējā cena, EUR/m²')]:
            if column in result:result[column]=result[column].round(0)
    return result
@st.cache_resource(show_spinner=False)
def load(path,version):
    with duckdb.connect(path,read_only=True) as con:return con.execute('SELECT * FROM transactions').df()

@st.cache_data(show_spinner=False)
def building_only_ids(path,version):
    with duckdb.connect(path,read_only=True) as con:
        return set(con.execute("""SELECT r.transaction_id FROM chosen_rows r
            JOIN transactions t USING(transaction_id)
            WHERE t.source_kind='built' AND t.valid AND t.category='Neklasificēts'
              AND NOT contains(t.flags,'cross_dataset')
            GROUP BY r.transaction_id
            HAVING bool_and(r.kind='built' AND coalesce(json_extract_string(r.payload,'$.ObjType'),'') IN ('E','EIB','IB'))
            """).fetchnumpy()['transaction_id'])
def fmt(value,decimals=0):
    return 'Nav aprēķināms' if value is None or pd.isna(value) else f'{value:,.{decimals}f}'.replace(',',' ').replace('.',',')
def reset():
    for key in list(st.session_state):
        if key.startswith('f_'):del st.session_state[key]

def choice(frame,column,label,key,target=None,format_func=str):
    options=sorted(v for v in frame[column].dropna().unique() if v)
    if key in st.session_state:
        st.session_state[key]=[v for v in st.session_state[key] if v in options]
    selected=(target if target is not None else st.sidebar).multiselect(label,options,key=key,placeholder='Visas vērtības',format_func=format_func)
    return frame[frame[column].isin(selected)] if selected else frame

def land_use_filter(frame):
    options=sorted(v for v in frame.use_code.dropna().unique() if v)
    selected=[v for v in st.session_state.get('f_land_use',[]) if v in options]
    st.session_state['f_land_use']=selected
    def change(code):
        values=set(st.session_state.get('f_land_use',[]))
        if st.session_state['nilm_option_'+code]:values.add(code)
        else:values.discard(code)
        st.session_state['f_land_use']=sorted(values)
    with st.sidebar.container(key='nilm_filter'):
        st.markdown('NĪLM — lietošanas mērķis')
        title='Visas vērtības' if not selected else f'Izvēlēti mērķi: {len(selected)}'
        with st.popover(title,width='stretch'):
            query=st.text_input('Meklēt lietošanas mērķi',key='nilm_search').strip().casefold()
            with st.container(height=360,border=False):
                for code in options:
                    name=land_use_name(code)
                    if query and query not in name.casefold():continue
                    key='nilm_option_'+code
                    st.session_state[key]=code in selected
                    st.checkbox(name,key=key,on_change=change,args=(code,))
        for code in selected:st.markdown(land_use_name(code))
    return frame[frame.use_code.isin(selected)] if selected else frame

def interval(frame,column,label,key):
    if frame[column].notna().sum()==0:return frame
    if st.sidebar.checkbox(label,key=key+'_on'):
        left,right=st.sidebar.columns(2)
        lo=left.number_input('No',min_value=0.,value=0.,key=key+'_lo')
        hi=right.number_input('Līdz',min_value=0.,value=max(1.,float(frame[column].max())),key=key+'_hi')
        return frame[frame[column].between(lo,hi)]
    return frame

def building_types(value):
    if pd.isna(value) or not str(value).strip():return frozenset(['Nav norādīts'])
    return frozenset(part.strip() for part in str(value).split('|') if part.strip()) or frozenset(['Nav norādīts'])

def building_type_filter(frame,target=None):
    types=frame.building_use.map(building_types)
    options=sorted(set().union(*types))
    key='f_building_use'
    if key in st.session_state:
        previous=set().union(*(building_types(value) for value in st.session_state[key]))
        st.session_state[key]=[value for value in options if value in previous]
    selected=st.session_state.get(key,[])
    def change(value):
        values=set(st.session_state.get(key,[]))
        if st.session_state['f_building_type_option_'+value]:values.add(value)
        else:values.discard(value)
        st.session_state[key]=sorted(values)
    def clear():st.session_state[key]=[]
    with (target if target is not None else st).container():
        st.markdown('Ēku tipi')
        title=f'Izvēlēti ēku tipi: {len(selected)}' if selected else 'Visi ēku tipi'
        with st.popover(title,width='stretch',help='Ja vēlaties atlasīt visus ēku tipus, nekas nav jāizvēlas.'):
            query=st.text_input('Meklēt ēku tipu',key='f_building_type_search').strip().casefold()
            st.button('Dzēst',key='f_building_type_clear',on_click=clear)
            with st.container(height=320,border=False):
                for value in options:
                    if query and query not in value.casefold():continue
                    option_key='f_building_type_option_'+value
                    st.session_state[option_key]=value in selected
                    st.checkbox(value,key=option_key,on_change=change,args=(value,))
        if selected:st.caption('; '.join(selected))
        result=frame[types.map(lambda values:bool(values.intersection(selected)))] if selected else frame
        total=int(frame.valid.sum()) if 'valid' in frame else len(frame)
        count=int(result.valid.sum()) if 'valid' in result else len(result)
        share=fmt(100*count/total,1)+' %' if total else '—'
        st.caption(f'{fmt(count)} no {fmt(total)} darījumiem · {share} no “Zeme ar ēkām” darījumiem ar pārējiem izvēlētajiem filtriem.')
    return result

def built_price_control():
    with st.container(key='built_price_control',border=True):
        st.radio('Kā aprēķināt vidējo cenu par m² (EUR/m2)?', ['Zemes platība','Ēku platība'],format_func=lambda value:'Darījuma summa ÷ zemes platība (m²)' if value=='Zemes platība' else 'Darījuma summa ÷ ēku kopplatība (m²)',horizontal=False,key='f_built_price_basis')

def filters(frame,category,primary_panel=None,building_panel=None,comparison=False):
    st.session_state['_price_unit']='EUR/ha' if category=='Zeme' else 'EUR/m²'
    inclusion_panel=None
    if category=='Zeme ar ēkām':
        inclusion_panel=(building_panel if building_panel is not None else st).container()
        include=inclusion_panel.checkbox('Iekļaut arī ēkas bez zemes',value=False,key='f_include_buildings_only',help='Ēkas; Ēkas un inženierbūves; Inženierbūves.')
        frame=frame.copy()
        frame['without_land']=False
        if include:
            state=current()
            ids=building_only_ids(str(DATA/state['database']),state['run_id'])
            extra=frame.transaction_id.isin(ids)
            frame.loc[extra,'source_category']=frame.loc[extra,'category']
            frame.loc[extra,'category']='Zeme ar ēkām'
            frame.loc[extra,'without_land']=True
    f=frame[frame.category.eq(category)] if category else frame.copy()
    panel=primary_panel if primary_panel is not None else st.sidebar
    # Keep sidebar slots stable when switching pages so calendar components
    # cannot be reconciled into the municipality selector's former position.
    district_panel=panel.container(key='district_choice')
    breakdown_panel=panel.container(key='breakdown_choice')
    date_panel=panel.container(key='compact_date_filters')
    compact_panel=panel.container(key='compact_local_filters')
    if not comparison:
        f=choice(f,'district','Izvēlies novadu/valstpilsētu','f_district',target=district_panel)
        if st.session_state.get('f_breakdown')=='Sadalījums pēc pagastiem':st.session_state['f_breakdown']='Sadalījums pēc pagastiem/pilsētām'
        breakdown_panel.radio('Teritoriju sadalījums',['Sadalījums pēc pagastiem/pilsētām','Sadalījums pēc ciematiem'],key='f_breakdown')
    if not comparison:
        for col,label in [('parish','Pagasts'),('city','Pilsēta'),('village','Ciems')]:
            f=choice(f,col,label,'f_'+col,target=compact_panel)
    previous_dates=st.session_state.pop('f_dates',None)
    if previous_dates:
        st.session_state.setdefault('f_date_from',previous_dates[0])
        if len(previous_dates)>1:st.session_state.setdefault('f_date_to',previous_dates[1])
    date_panel.markdown('**Darījuma datums**')
    for date_key in ('f_date_from','f_date_to'):
        if date_key in st.session_state and st.session_state[date_key]<date(2012,1,1):st.session_state[date_key]=date(2012,1,1)
    with date_panel:
        date_from=latvian_date_input('No','f_date_from',date(2012,1,1))
        date_to=latvian_date_input('Līdz','f_date_to',date.today())
    if date_from>date_to:panel.warning('Datumam “No” jābūt pirms datuma “Līdz” vai vienādam ar to.')
    st.session_state.pop('f_year',None)
    f=f[pd.to_datetime(f.date).between(pd.Timestamp(date_from),pd.Timestamp(date_to))]
    for suffix in ('','_on','_lo','_hi'):
        st.session_state.pop('f_amount'+suffix,None)
    if category=='Zeme':
        f=land_use_filter(f)
        for key in ('f_land_area','f_land_count','f_agriculture_area','f_forest_area','f_price_ha','f_outliers'):
            for suffix in ('','_on','_lo','_hi'):st.session_state.pop(key+suffix,None)
    elif category=='Zeme ar ēkām':
        for key in ('f_land_area','f_building_count','f_building_area','f_build_year'):
            for suffix in ('','_on','_lo','_hi'):
                st.session_state.pop(key+suffix,None)
    elif category=='Dzīvokļi':
        for key in ('f_rooms','f_floor','f_area','f_price_m2','f_premise_count','f_flat_year'):
            for suffix in ('','_on','_lo','_hi'):
                st.session_state.pop(key+suffix,None)
    if category=='Zeme ar ēkām':
        f=building_type_filter(f,building_panel)
        counted=f[f.valid]
        total=len(counted)
        without=int(counted.without_land.sum())
        with_land=total-without
        pct=lambda n:fmt(100*n/total,1)+' %' if total else '—'
        inclusion_panel.caption(f'Atlasē {fmt(total)} darījumi: ar zemi — {fmt(with_land)} ({pct(with_land)}); bez zemes — {fmt(without)} ({pct(without)}). Piemēroti visi izvēlētie filtri.')
        if st.session_state.get('page','Zeme') not in CATEGORIES:built_price_control()
        basis=st.session_state.get('f_built_price_basis','Zemes platība')
        f=built_unit_prices(f,'land_area' if basis=='Zemes platība' else 'building_area')
    f=f.copy()
    f.attrs['price_unit']=st.session_state['_price_unit']
    return f

def price_chart_filter(frame):
    enabled=st.checkbox('Izslēgt netipiski zemas un augstas cenas',key='f_statistical_outliers',help='Tikai cenu grafikā izslēdz darījumus ar neparasti zemu vai augstu cenu par m² vai ha.\n\nFormula: izslēdz cenas, kuru logaritms ir ārpus Q1 − 1,5 × IQR līdz Q3 + 1,5 × IQR. Q1 un Q3 ir cenu logaritmu 25. un 75. procentile; IQR = Q3 − Q1.')
    if not enabled:return frame
    result=mark_statistical_outliers(frame,by_village=st.session_state.get('f_breakdown')=='Sadalījums pēc ciematiem')
    total=int(frame.valid.sum()) if 'valid' in frame else len(frame)
    st.caption(f'Šajā cenu grafikā izslēgti {fmt(int(result.exclude_price_stats.sum()))} darījumi no {fmt(total)} darījumiem.')
    return result

def chart_download_config(fig):
    # Keep a single, always-visible PNG download control beside the chart title.
    fig.update_layout(modebar=dict(orientation='h',bgcolor='rgba(0,0,0,0)',color='#285b52',activecolor='#164e45'))
    return {'displayModeBar':True,'displaylogo':False,'modeBarButtons':[['toImage']],
            'toImageButtonOptions':{'format':'png','filename':fig.layout.title.text or 'grupa93-grafiks','width':1400,'height':fig.layout.height or 600,'scale':2}}


def chart(fig,amounts_in_millions=False,amount_scale=1_000_000):
    is_bar=any(trace.type in ('bar','histogram') for trace in fig.data)
    fig.update_layout(template='plotly_white',font=dict(family='Arial',size=14 if is_bar else 12),margin=dict(l=10,r=10,t=55 if is_bar else 35,b=20 if is_bar else 10),height=480 if is_bar else 300,colorway=['#285b52','#9b7650'],paper_bgcolor='white')
    fig.update_traces(marker_color='#285b52',selector=dict(type='bar'))
    for trace in fig.data:
        if trace.type=='bar':
            labels=[(chart_number(value/amount_scale,1) if amounts_in_millions else (f'{value:,.0f}' if float(value).is_integer() else f'{value:,.2f}').replace(',', ' ').replace('.', ',')) if pd.notna(value) else '' for value in trace.y]
            trace.update(text=labels,texttemplate='%{text}',textposition='outside',cliponaxis=False,textfont=dict(size=13),textangle=0)
            trace.update(customdata=[fmt(v,2 if amounts_in_millions else 0) for v in trace.y],hovertemplate='%{x}<br>%{customdata}'+(' EUR' if amounts_in_millions else '')+'<extra></extra>')
        elif trace.type=='histogram':
            trace.update(texttemplate='%{y}',textposition='outside',cliponaxis=False,textfont=dict(size=12))
    if is_bar:
        fig.update_layout(uniformtext=dict(minsize=10,mode='show'))
    if amounts_in_millions:
        maximum=max((float(value) for trace in fig.data if trace.type=='bar' for value in trace.y if pd.notna(value)),default=0)
        fig.update_yaxes(range=[0,maximum*1.18 if maximum>0 else 1])
        fig.update_layout(title_text=fig.layout.title.text+(', tūkst. EUR' if amount_scale==1000 else ', milj. EUR'))
    fig.update_layout(legend=dict(orientation='h',y=-0.25,title_text=''))
    st.plotly_chart(fig,width='stretch',theme=None,config=chart_download_config(fig))

def cards(frame,category):
    m=metrics(frame)
    cols=st.columns(4)
    for col,label,value in zip(cols,['Darījumu skaits','Darījumu summa, EUR','Mediānas darījuma summa, EUR','Vidējā darījuma summa, EUR'],[m['count'],m['sum'],m['median_amount'],m['mean_amount']]):col.metric(label,fmt(value))
    if category=='Zeme ar ēkām':
        a,b,c=st.columns(3);a.metric('Zināmā zemes platība, m²',fmt(frame.land_area.sum(min_count=1)));b.metric('Zināmā būvju platība, m²',fmt(frame.building_area.sum(min_count=1)));c.metric(price_label('Vidējā cena, EUR/m²'),fmt(m['mean_price'],price_decimals()))
        st.caption(f'Cenas aprēķina izlase: {m["price_n"]} no {m["count"]} darījumiem. Platība: '+st.session_state.get('f_built_price_basis','Zemes platība')+'.')
    else:
        a,b,c,d=st.columns(4)
        a.metric('Zināmā pārdotā platība, m²',fmt(m['area'],1));b.metric(price_label('Mediānas cena, EUR/m²'),fmt(m['median_price'],price_decimals()));c.metric(price_label('Svērtā cena, EUR/m²'),fmt(m['weighted_price'],price_decimals()));d.metric(price_label('Vidējā cena, EUR/m²'),fmt(m['mean_price'],price_decimals()))
        if category=='Zeme':st.caption('Mediānas cena, EUR/ha: '+fmt(m['median_price'] if pd.notna(m['median_price']) else None,0)+' · attiecas uz visu pārdoto zemes platību, nevis tikai mežu vai LIZ.')
        st.caption(f'Platība pieejama {m["area_n"]} no {m["count"]} darījumiem; cenu aprēķina izlase: {m["price_n"]}.')
    if m['count']<CONFIG['small_sample']:st.warning('Neliels darījumu skaits. Rezultāti nav stabili vispārināšanai.')
    elif category!='Zeme ar ēkām' and m['price_n']<CONFIG['small_sample']:st.warning('Neliels cenu aprēķina darījumu skaits.')

def table(frame,state):
    st.subheader('Darījumu atlase')
    price_column='price_ha' if st.session_state.get('_price_unit')=='EUR/ha' else 'price_m2'
    display=['transaction_id','date','category','district','parish','city','village','address','object_count','land_area','building_area','area','amount',price_column,'flags','origin']
    st.dataframe(frame[display],hide_index=True,width='stretch',height=350,column_config={'transaction_id':'Darījuma ID','date':'Datums','category':'Kategorija','district':'Novads / valstspilsēta','address':'Adrese','object_count':'Objekti','amount':st.column_config.NumberColumn('Summa, EUR',format='%.2f'),'area':st.column_config.NumberColumn('Cenas aprēķina platība, m²',format='%.2f'),price_column:st.column_config.NumberColumn(price_label('EUR/m²'),format='%.0f' if price_decimals()==0 else '%.2f'),'flags':'Kvalitātes pazīmes'})
    a,b=st.columns(2)
    a.download_button('Lejupielādēt CSV',export_csv(frame),'grupa93-darijumi.csv','text/csv',width='stretch')
    # Excel generation is explicit to avoid rebuilding a large workbook on every filter change.
    if b.button('Sagatavot Excel',width='stretch'):
        with st.spinner('Eksporta sagatavošana…'):
            content=export_excel(frame,{'Avots':'Valsts zemes dienests, CC BY 4.0','Atjaunots':state['updated_at'],'Filtri':{k:str(v) for k,v in st.session_state.items() if k.startswith('f_')},'Metodika':'Viena summa katram DeaId. Cenas tikai piemērotām platībām. Pilnā metodika: METHODOLOGY_LV.md','Darījumu skaits':len(frame)})
        b.download_button('Lejupielādēt Excel',content,'grupa93-darijumi.xlsx','application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',width='stretch')
    tid=st.text_input('Darījuma sastāvs — ievadiet ID')
    if tid:
        if tid not in set(frame.transaction_id):st.info('ID nav pašreizējā atlasē.')
        else:
            with duckdb.connect(str(DATA/state['database']),read_only=True) as con:
                st.dataframe(con.execute('SELECT * FROM transaction_objects WHERE transaction_id=?',[tid]).df(),hide_index=True)
                raw=con.execute('SELECT origin,kind,payload FROM raw_records WHERE transaction_id=?',[tid]).fetchall()
                with st.expander('Avota rindas un izcelsme'):st.json([{'origin':o,'kind':k,'row':json.loads(p)} for o,k,p in raw])

def map_view(frame,state):
    path=DATA/state['raw']/'map.geojson'
    if not path.exists():st.info('Oficiālā ģeometrija nav pieejama.');return
    st.caption('Karte: pašreizējie pašvaldību kodi. Nezināmās teritorijas nav iekrāsotas. Klikšķis izvēlas teritoriju.')
    grouped=aggregate(frame[frame.municipality_atr.ne('')],['municipality_atr','district'])
    if grouped.empty:st.info('Atlasē nav droši piesaistītu teritoriju.');return
    labels={'count':'Darījumu skaits','sum':'Darījumu summa','median_price':price_label('Mediānas cena, EUR/m²'),'weighted_price':price_label('Svērtā cena, EUR/m²')}
    metric=st.selectbox('Kartes rādītājs',list(labels),format_func=labels.get)
    if grouped[metric].notna().sum()==0:st.info('Nav aprēķināms šai darījumu izlasei.');return
    maximum=float(grouped[metric].max())
    positives=grouped.loc[grouped[metric].gt(0),metric]
    if maximum>0 and not positives.empty:
        zero_edge=min(float(positives.min())/maximum/2,0.49)
        map_scale=[[0,'#e5e8e7'],[zero_edge,'#e5e8e7'],[zero_edge,'#e8efec'],[1,'#285b52']]
    else:
        map_scale=[[0,'#e5e8e7'],[1,'#e5e8e7']]
    fig=px.choropleth(grouped,geojson=json.loads(path.read_text(encoding='utf8')),locations='municipality_atr',featureidkey='properties.atrib',color=metric,hover_name='district',hover_data=['count','price_n'],custom_data=['district'],color_continuous_scale=map_scale,range_color=(0,max(maximum,1e-9)),labels={**labels,'municipality_atr':'Pašvaldības kods','price_n':'Cenu izlase'})
    fig.update_geos(fitbounds='locations',visible=False);fig.update_layout(height=420,margin=dict(l=0,r=0,t=0,b=0))
    event=st.plotly_chart(fig,width='stretch',on_select='rerun',key='territory_map')
    if event and event.selection.points:
        district=event.selection.points[0].get('customdata',[''])[0]
        if st.button('Atlasīt: '+district):st.session_state['map_pending']=district;st.rerun()

@st.cache_data(show_spinner=False)
def parish_catalog(path,version):
    with duckdb.connect(path,read_only=True) as con:
        return con.execute("""SELECT p.KODS AS code,p.ATRIB AS atr,p.NOSAUKUMS AS territory,p.TIPS_CD AS kind,m.NOSAUKUMS AS district
            FROM territories p JOIN territories m ON p.VKUR_CD=m.KODS
            WHERE p.TIPS_CD IN ('105','104') AND p.STATUSS='EKS' AND m.STATUSS='EKS' AND m.TIPS_CD='113'
            UNION ALL
            SELECT KODS,ATRIB,NOSAUKUMS,TIPS_CD,NOSAUKUMS FROM territories
            WHERE TIPS_CD='104' AND VKUR_TIPS='101' AND STATUSS='EKS'""").df().drop_duplicates()

@st.cache_data(show_spinner=False)
def village_catalog(path,version):
    with duckdb.connect(path,read_only=True) as con:
        return con.execute("""SELECT v.KODS AS code,v.ATRIB AS atr,
            v.NOSAUKUMS || ' · ' || p.NOSAUKUMS AS territory,v.TIPS_CD AS kind,
            m.NOSAUKUMS AS district
            FROM territories v JOIN territories p ON v.VKUR_CD=p.KODS
            JOIN territories m ON p.VKUR_CD=m.KODS
            WHERE v.TIPS_CD='106' AND v.STATUSS='EKS' AND p.STATUSS='EKS' AND m.STATUSS='EKS'
            """).df().drop_duplicates()

@st.cache_data(show_spinner=False)
def local_geometry(path,modified):
    return json.loads(path.read_text(encoding='utf8'))

def local_map_figure(result,catalog,geo,metric,district_geo=None):
    mapped=result.merge(catalog[['district','territory','atr']],left_on=['Novads','Teritorija'],right_on=['district','territory'],how='inner',validate='one_to_one')
    features=[f for f in geo['features'] if f['properties']['atr'] in set(mapped.atr)]
    selected_geo={'type':'FeatureCollection','features':features}
    available={f['properties']['atr'] for f in features}
    mapped=mapped[mapped.atr.isin(available)].copy()
    if mapped.empty:return None,0
    mapped['Krāsa']=mapped[metric]/1_000_000 if metric=='Summa, EUR' else mapped[metric]
    title='Summa, milj. EUR' if metric=='Summa, EUR' else metric
    fig=px.choropleth(mapped,geojson=selected_geo,locations='atr',featureidkey='properties.atr',color_discrete_sequence=['#e3e7e5'],hover_name='Teritorija')
    fig.update_traces(showlegend=False,marker_line_color='#ffffff',marker_line_width=1,hovertemplate='%{hovertext}<br>Nav aprēķināms<extra></extra>')
    background_features=[f for f in geo['features'] if f['properties']['atr'] not in available]
    if background_features:
        fig.add_trace(go.Choropleth(
            geojson={'type':'FeatureCollection','features':background_features},
            locations=[f['properties']['atr'] for f in background_features],
            featureidkey='properties.atr',z=[0]*len(background_features),
            colorscale=[[0,'#e5e7e6'],[1,'#e5e7e6']],zmin=0,zmax=1,
            showscale=False,showlegend=False,hoverinfo='skip',
            marker_line_color='#f7f8f7',marker_line_width=0.4,
            name='Pārējā Latvija'))
        fig.data=(fig.data[-1],)+fig.data[:-1]
    # Keep exact zero values grey instead of assigning the lightest green.
    colored=mapped[mapped['Krāsa'].notna() & mapped['Krāsa'].ne(0)]
    if not colored.empty:
        layer=px.choropleth(colored,geojson=selected_geo,locations='atr',featureidkey='properties.atr',color='Krāsa',hover_name='Teritorija',hover_data={'atr':False,'Krāsa':':.1f' if metric=='Summa, EUR' else ':,.0f' if metric==price_label('Vidējā cena, EUR/m²') and price_decimals()==0 else ':.2f' if metric==price_label('Vidējā cena, EUR/m²') else ':.0f','Darījumu skaits':True,'Cenu izlases apjoms':True},labels={'Krāsa':title},range_color=(0,max(float(colored['Krāsa'].max()),1e-9)),color_continuous_scale=['#edf5ec','#b4d5b0','#559a76','#164e45'])
        layer.update_traces(marker_line_color='#ffffff',marker_line_width=1)
        fig.add_traces(layer.data)
        fig.update_layout(coloraxis=layer.layout.coloraxis)
    names=mapped.set_index('atr')['Teritorija'].to_dict()
    values=mapped.set_index('atr')['Krāsa'].to_dict()
    maximum=float(colored['Krāsa'].max()) if not colored.empty else 0
    points=[shape(feature['geometry']).representative_point() for feature in features]
    fig.add_trace(go.Scattergeo(lon=[p.x for p in points],lat=[p.y for p in points],mode='text',
        text=[names[f['properties']['atr']].replace(' pag.','<br>pag.') for f in features],
        textfont=dict(size=11,color=['white' if pd.notna(values[f['properties']['atr']]) and maximum>0 and values[f['properties']['atr']]/maximum>0.6 else '#173c34' for f in features]),
        hoverinfo='skip',showlegend=False))
    if district_geo:
        district_features=district_geo['features']
        border_lon,border_lat=[],[]
        for feature in district_features:
            geometry=shape(feature['geometry'])
            polygons=list(geometry.geoms) if geometry.geom_type=='MultiPolygon' else [geometry]
            for polygon in polygons:
                for ring in [polygon.exterior,*polygon.interiors]:
                    border_lon.extend([point[0] for point in ring.coords]+[None])
                    border_lat.extend([point[1] for point in ring.coords]+[None])
        fig.add_trace(go.Scattergeo(
            lon=border_lon,lat=border_lat,mode='lines',
            line=dict(color='#595959',width=1.4),connectgaps=False,
            hoverinfo='skip',showlegend=False,name='Novadu robežas'))
        district_points=[shape(f['geometry']).representative_point() for f in district_features]
        fig.add_trace(go.Scattergeo(
            lon=[p.x for p in district_points],lat=[p.y for p in district_points],
            mode='text',text=['<b>'+f['properties']['nosaukums'].replace(' novads','<br>novads')+'</b>' for f in district_features],
            textposition='top center',textfont=dict(size=12,color='#515b56'),
            hoverinfo='skip',showlegend=False,name='Novadu nosaukumi'))
    bounds=[shape(feature['geometry']).bounds for feature in features]
    west,south=min(b[0] for b in bounds),min(b[1] for b in bounds)
    east,north=max(b[2] for b in bounds),max(b[3] for b in bounds)
    lon_padding=max((east-west)*0.08,0.01)
    lat_padding=max((north-south)*0.08,0.01)
    fig.update_geos(fitbounds=False,visible=False,projection_type='mercator',
        lonaxis_range=[west-lon_padding,east+lon_padding],
        lataxis_range=[south-lat_padding,north+lat_padding],
        center=dict(lon=(west+east)/2,lat=(south+north)/2))
    fig.update_layout(height=650,margin=dict(l=0,r=0,t=15,b=0),paper_bgcolor='white')
    return fig,len(mapped)

def local_map(result,catalog):
    st.subheader('Novada teritoriju karte')
    metric=st.selectbox('Rādītājs kartē',['Darījumu skaits',price_label('Vidējā cena, EUR/m²'),'Summa, EUR'],key='f_local_map_metric')
    path=DATA/'reference/local-boundaries.geojson'
    if not path.exists():st.info('Teritoriju karte šobrīd nav pieejama.');return
    geo=local_geometry(path,path.stat().st_mtime_ns)
    district_path=DATA/current()['raw']/'map.geojson'
    district_geo=local_geometry(district_path,district_path.stat().st_mtime_ns) if district_path.exists() else None
    fig,matched=local_map_figure(result,catalog,geo,metric,district_geo)
    if fig is None:st.info('Izvēlētajām teritorijām nav pieejamas robežas.');return
    st.plotly_chart(fig,width='stretch',theme=None,key='local_territory_map')
    st.caption("Tumšāka krāsa — lielāka vērtība. Pelēks — cena nav aprēķināma.")
    if matched<len(catalog):st.caption(f'Kartē piesaistītas {matched} no {len(catalog)} teritorijām.')

def territory_rows(frame,catalog):
    rows=[]
    assigned=pd.Series(False,index=frame.index)
    for territory in catalog.sort_values(['kind','district','territory'],ascending=[False,True,True]).itertuples(index=False):
        field={'105':'parish_code','104':'city_code','106':'village_code'}[territory.kind]
        mask=frame[field].eq(territory.code) & frame.district.eq(territory.district) & ~assigned
        assigned|=mask
        values=metrics(frame[mask])
        rows.append({'Novads':territory.district,'Teritorija':territory.territory,'Veids':{'105':'Pagasts','104':'Pilsēta','106':'Ciems'}[territory.kind],'Darījumu skaits':values['count'],price_label('Vidējā cena, EUR/m²'):values['mean_price'],'Summa, EUR':values['sum'] if values['count'] else 0,'Vidējā summa, EUR':values['mean_amount'],'Cenu izlases apjoms':values['price_n']})
    for district,subset in frame[~assigned].groupby('district'):
        values=metrics(subset)
        rows.append({'Novads':district,'Teritorija':'Ārpus ciemiem / ciems nav noteikts' if catalog.kind.eq('106').any() else 'Nav precīzas piesaistes','Veids':'Nezināms','Darījumu skaits':values['count'],price_label('Vidējā cena, EUR/m²'):values['mean_price'],'Summa, EUR':values['sum'],'Vidējā summa, EUR':values['mean_amount'],'Cenu izlases apjoms':values['price_n']})
    return pd.DataFrame(rows).sort_values(['Novads','Veids','Teritorija'])

def district_rows(frame,catalog):
    rows=[]
    for district in sorted(catalog.district.unique()):
        values=metrics(frame[frame.district.eq(district)])
        rows.append({'Novads':district,'Teritorija':district+' — kopumā','Veids':'Novads kopumā','Darījumu skaits':values['count'],price_label('Vidējā cena, EUR/m²'):values['mean_price'],'Summa, EUR':values['sum'] if values['count'] else 0,'Vidējā summa, EUR':values['mean_amount'],'Cenu izlases apjoms':values['price_n']})
    return pd.DataFrame(rows)

def territory_yearly(frame,catalog,years,include_districts=False):
    rows=[]
    for year in years:
        annual=territory_rows(frame[frame.year.eq(year)],catalog)
        if include_districts:annual=pd.concat([annual,district_rows(frame[frame.year.eq(year)],catalog)],ignore_index=True)
        annual['Gads']=year
        rows.append(annual)
    return pd.concat(rows,ignore_index=True) if rows else pd.DataFrame()

def territory_comparison_figure(data,annual,years,field,title,amount_scale=1_000_000):
    data=data.sort_values(field,na_position='first').copy()
    outside=data.Teritorija.eq('Ārpus ciemiem / ciems nav noteikts')
    district=data.Veids.eq('Novads kopumā')
    # The first numeric y position is the bottom row of the horizontal chart.
    data=pd.concat([data[outside],data[~outside & ~district],data[district]],ignore_index=True)
    names=data['Nosaukums'].tolist()
    keys=pd.MultiIndex.from_frame(data[['Novads','Teritorija']])
    scale=amount_scale if field=='Summa, EUR' else 1
    decimals=1 if field=='Summa, EUR' else price_decimals() if field==price_label('Vidējā cena, EUR/m²') else 0
    columns=[]
    full_columns=[]
    full_decimals=0 if field=='Darījumu skaits' or (field==price_label('Vidējā cena, EUR/m²') and price_decimals()==0) else 2
    unit=' EUR' if field=='Summa, EUR' else price_label(' EUR/m²') if field==price_label('Vidējā cena, EUR/m²') else ''
    for year in years:
        values=annual[annual.Gads.eq(year)].set_index(['Novads','Teritorija'])[field].reindex(keys)
        if field!=price_label('Vidējā cena, EUR/m²'):values=values.fillna(0)
        columns.append((values/scale).tolist())
        full_columns.append([chart_number(value,full_decimals)+unit if pd.notna(value) else 'Nav aprēķināms' for value in values])
    matrix=list(map(list,zip(*columns)))
    totals=data[field]/scale
    fig=make_subplots(rows=1,cols=2,shared_yaxes=True,column_widths=[.27,.73],horizontal_spacing=.06,subplot_titles=['Izvēlētais periods',''])
    district_mask=data.Veids.eq('Novads kopumā')
    outside_mask=data.Teritorija.eq('Ārpus ciemiem / ciems nav noteikts')
    scale_mask=~district_mask & ~outside_mask
    split=int((~district_mask).sum())
    has_gap=district_mask.any() and (~district_mask).any()
    row_positions=[i+0.67 if has_gap and i>=split else i for i in range(len(names))]
    local_totals=totals[scale_mask]
    maximum=float(local_totals.max()) if local_totals.notna().any() else 0
    bar_limit=maximum*1.15 if maximum>0 else 0.8
    for is_district,color in [(False,'#285b52'),(True,'#9b7650')]:
        mask=district_mask if is_district else ~district_mask
        if not mask.any():continue
        selected=data.loc[mask]
        limited=district_mask | outside_mask
        widths=totals.where(~limited,totals.clip(upper=bar_limit))
        fig.add_trace(go.Bar(x=widths[mask],y=[row_positions[i] for i in selected.index],hovertext=selected.Nosaukums,orientation='h',marker_color=color,width=0.8,
            text=[chart_number(totals[i],decimals)+(' ▸' if limited[i] and totals[i]>bar_limit else '') for i in selected.index],
            customdata=[chart_number(v,full_decimals)+unit if pd.notna(v) else 'Nav aprēķināms' for v in selected[field]],
            textposition='outside',cliponaxis=False,
            hovertemplate='%{hovertext}<br>Izvēlētais periods: %{customdata}<extra></extra>'),row=1,col=1)
    fig.update_layout(barmode='overlay')
    axis_names=names.copy()
    if district_mask.any() and (~district_mask).any():
        split=int((~district_mask).sum())
        axis_names.insert(split,' ')
        # Mask the background and grid lines across the spacer row in both panels.
        for axis_ref,domain in [('y',fig.layout.xaxis.domain),('y2',fig.layout.xaxis2.domain)]:
            fig.add_shape(type='rect',xref='paper',yref=axis_ref,x0=domain[0]-0.002,x1=domain[1]+0.002,
                y0=split-0.5,y1=split+0.17,fillcolor='white',line_width=0,layer='above')
    row_edges=[-0.5]
    for i in range(len(names)):
        if has_gap and i==split:row_edges.append(split+0.17)
        row_edges.append(row_positions[i]+0.5)
    full_matrix=list(map(list,zip(*full_columns)))
    for is_district,palette in [(False,['#edf5ec','#b4d5b0','#559a76','#164e45']),(True,['#f5eee5','#d6bc9c','#9b7650','#654629'])]:
        indices=[i for i,kind in enumerate(data.Veids) if (kind=='Novads kopumā')==is_district]
        if not indices:continue
        scale_indices=indices if is_district else [i for i in indices if scale_mask[i]]
        color_max=max((v for i in scale_indices for v in matrix[i] if pd.notna(v)),default=1) or 1
        # Both color layers share the complete row grid, including masked cells.
        # A one-row categorical heatmap otherwise infers incompatible cell bounds.
        # Put exact zeros in a dedicated grey band on the same heatmap.
        # A single layer avoids transparent masked cells covering zero cells.
        positive_values=[v for i in indices for v in matrix[i] if pd.notna(v) and v>0]
        edge=min(min(positive_values)/color_max/2,0.49) if positive_values else 0.5
        colors=[[0,'#e5e8e7'],[edge,'#e5e8e7'],[edge,palette[0]]]
        colors.extend([[edge+(1-edge)*j/(len(palette)-1),palette[j]] for j in range(1,len(palette)-1)])
        colors.append([1.0,palette[-1]])
        cells=[[0 if pd.isna(v) or v==0 else v for v in matrix[i]] if i in indices else [None]*len(years) for i in range(len(names))]
        labels=[[chart_number(v,decimals) if pd.notna(v) else '—' for v in matrix[i]] if i in indices else ['']*len(years) for i in range(len(names))]
        hover_values=[row.copy() for row in full_matrix]
        if len(axis_names)>len(names):
            cells.insert(split,[None]*len(years))
            labels.insert(split,['']*len(years))
            hover_values.insert(split,['']*len(years))
        fig.add_trace(go.Heatmap(z=cells,x=[str(y) for y in years],y=row_edges,text=labels,customdata=hover_values,hovertext=[[name]*len(years) for name in axis_names],texttemplate='%{text}',textfont=dict(size=10),colorscale=colors,autocolorscale=False,zmin=0,zmax=color_max,showscale=False,xgap=2,ygap=2,hoverongaps=False,hovertemplate='%{hovertext}<br>%{x}: %{customdata}<extra></extra>'),row=1,col=2)
    fig.update_xaxes(range=[0,maximum*1.45 if maximum>0 else 1],row=1,col=1)
    fig.update_xaxes(type='category',side='top',tickmode='array',tickvals=[str(y) for y in years],tickangle=-45 if len(years)>12 else 0,row=1,col=2)
    fig.update_yaxes(type='linear',tickmode='array',tickvals=row_positions,ticktext=names,showline=False,zeroline=False,range=[-0.5,len(names)-0.5+(0.67 if has_gap else 0)],autorange=False,automargin=True)
    fig.update_layout(title=title,template='plotly_white',height=max(440,34*len(data)+160),margin=dict(l=10,r=15,t=115,b=30),font=dict(size=12),showlegend=False)
    return fig

def territory_charts(result,category,frame,catalog):
    plot=pd.concat([result,district_rows(frame,catalog)],ignore_index=True)
    plot['Nosaukums']=plot['Teritorija']+' · '+plot['Novads'] if plot['Novads'].nunique()>1 else plot['Teritorija']
    plot.loc[plot.Veids.eq('Novads kopumā'),'Nosaukums']=plot.loc[plot.Veids.eq('Novads kopumā'),'Teritorija']
    start=st.session_state.get('f_date_from',date(2012,1,1)).year
    end=st.session_state.get('f_date_to',date.today()).year
    years=list(range(start,end+1))
    if not years:return
    annual=territory_yearly(frame,catalog,years,include_districts=True)
    for field,title in [('Darījumu skaits','Darījumu skaits'),('Summa, EUR','Darījumu summa, tūkst. EUR' if category=='Dzīvokļi' else 'Darījumu summa, milj. EUR'),(price_label('Vidējā cena, EUR/m²'),price_label('Vidējā cena, EUR/m²'))]:
        if category=='Zeme ar ēkām' and field==price_label('Vidējā cena, EUR/m²'):
            built_price_control()
            title+=' · '+st.session_state.get('f_built_price_basis','Zemes platība')
        chart_plot=plot
        chart_annual=annual
        if field==price_label('Vidējā cena, EUR/m²'):
            price_frame=price_chart_filter(frame)
            if 'exclude_price_stats' in price_frame:
                chart_plot=pd.concat([territory_rows(price_frame,catalog),district_rows(price_frame,catalog)],ignore_index=True)
                chart_plot['Nosaukums']=chart_plot['Teritorija']+' · '+chart_plot['Novads'] if chart_plot['Novads'].nunique()>1 else chart_plot['Teritorija']
                chart_plot.loc[chart_plot.Veids.eq('Novads kopumā'),'Nosaukums']=chart_plot.loc[chart_plot.Veids.eq('Novads kopumā'),'Teritorija']
                chart_annual=territory_yearly(price_frame,catalog,years,include_districts=True)
        data=chart_plot[chart_plot[field].notna()].sort_values(field)
        if data.empty:
            st.info('Vidējā cena: atlasē nav piemērotu darījumu.');continue
        fig=territory_comparison_figure(data,chart_annual,years,field,title,amount_scale=1000 if category=='Dzīvokļi' else 1_000_000)
        if field==price_label('Vidējā cena, EUR/m²'):
            fig.update_layout(title=dict(y=1,yanchor='top',pad=dict(t=0,b=0)),margin=dict(t=85))
        st.plotly_chart(fig,width='stretch',theme=None,config=chart_download_config(fig))

def parish_summary(frame,category,state):
    selected=st.session_state.get('f_district',[])
    if not selected:return
    villages=st.session_state.get('f_breakdown','Sadalījums pēc pagastiem/pilsētām')=='Sadalījums pēc ciematiem'
    catalog=(village_catalog if villages else parish_catalog)(str(DATA/state['database']),state['run_id'])
    catalog=catalog[catalog.district.isin(selected)]
    if catalog.empty:
        st.info('Izvēlētajā novadā nav reģistrētu ciemu.' if villages else 'Nav pieejamu teritoriju.');return
    result=territory_rows(frame,catalog)
    territory_charts(result,category,frame,catalog)
    table_result=result.drop(columns=['Veids','Cenu izlases apjoms'])
    export_result=table_result.round({'Summa, EUR':0,'Vidējā summa, EUR':2,price_label('Vidējā cena, EUR/m²'):price_decimals()})
    st.download_button('Lejupielādēt Excel',export_excel(export_result,{'Avots':'Valsts zemes dienests, CC BY 4.0','Kategorija':category,'Atjaunots':state['updated_at'],'Filtri':{k:str(v) for k,v in st.session_state.items() if k.startswith('f_')}}),'grupa93-teritorijas.xlsx','application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',key='territory_table_download')
    st.dataframe(table_result,hide_index=True,width='stretch',height=min(700,38+35*len(result)),column_config={price_label('Vidējā cena, EUR/m²'):st.column_config.NumberColumn(format='%.0f' if price_decimals()==0 else '%.2f'),'Summa, EUR':st.column_config.NumberColumn(format='%.0f'),'Vidējā summa, EUR':st.column_config.NumberColumn(format='%.2f')})
    if category=='Zeme ar ēkām':st.caption('Cenas aprēķina platība: '+st.session_state.get('f_built_price_basis','Zemes platība')+'. Cena ietver visu darījuma īpašumu.')
    if villages:
        st.caption('Ciemu robežas pašreizējā kartes datu avotā nav pieejamas. Darījumi ārpus ciemiem vai bez noteikta ciema ir atsevišķā rindā un iekļauti novada kopsummā.')
    else:local_map(result,catalog)

def analysis(frame,category,state,primary_panel=None,building_panel=None):
    f=filters(frame,category,primary_panel,building_panel);valid=f[f.valid]
    selected=st.session_state.get('f_district',[])
    with st.container(key='territory_section',border=True):
        st.markdown('<div class="section-heading"><span>01</span><h2>Teritoriālais iedalījums</h2></div>',unsafe_allow_html=True)
        if not selected:st.markdown('<div class="district-selection-hint">Izvēlieties novadu kreisajā pusē, lai redzētu teritoriju salīdzinājumu.</div>',unsafe_allow_html=True)
        else:parish_summary(valid,category,state)
    if not selected:
        overview(f,valid,category,state)

def overview(f,valid,category,state):
    if valid.empty:st.info('Filtri neatgrieza derīgus darījumus. Mainiet filtrus vai atjaunojiet sākotnējo atlasi.');return
    yearly=aggregate(valid,['year'])
    chart(px.bar(yearly,x='year',y='count',title='Darījumu skaits pa gadiem',labels={'year':'Gads','count':'Darījumi'}))
    chart(px.bar(yearly,x='year',y='sum',title='Darījumu summa pa gadiem',labels={'year':'Gads','sum':'EUR'}),amounts_in_millions=True,amount_scale=1000 if category=='Dzīvokļi' else 1_000_000)
    if category=='Zeme ar ēkām':built_price_control()
    if valid.price_m2.notna().any():
        price_yearly=aggregate(price_chart_filter(valid),['year'])
        chart(px.line(present(price_yearly),x='Gads',y=[price_label('Mediānas cena, EUR/m²'),price_label('Vidējā cena, EUR/m²')],markers=True,title=price_label('Mediānas un vidējā cena, EUR/m²'),labels={'variable':'Rādītājs','value':price_label('EUR/m²')},color_discrete_sequence=['#285b52','#9b7650']))
    yearly_table=present(yearly[['year','count','sum','mean_amount','mean_price']])
    st.dataframe(yearly_table,hide_index=True,width='stretch',column_config={'Gads':st.column_config.NumberColumn(format='%d'),'Darījumu skaits':st.column_config.NumberColumn(format='%d'),'Summa, EUR':st.column_config.NumberColumn(format='%.0f'),'Vidējā summa, EUR':st.column_config.NumberColumn(format='%.2f'),price_label('Vidējā cena, EUR/m²'):st.column_config.NumberColumn(format='%.0f' if price_decimals()==0 else '%.2f')})
    st.caption('Nepilni gadi nav tieši salīdzināmi ar pilniem gadiem. Vidējā cena aprēķināta darījumiem ar zināmu, piemērotu platību.')

def select_page(page):
    previous=st.session_state.get('page','Zeme')
    common=['f_district','f_parish','f_city','f_village','f_dates','f_date_from','f_date_to','f_breakdown']
    preserved={key:st.session_state[key] for key in common if key in st.session_state} if previous in CATEGORIES and page in CATEGORIES else {}
    st.session_state['page']=page
    reset()
    st.session_state.update(preserved)

def main():
    st.set_page_config(page_title='Grupa93 | NĪ darījumi',layout='wide')
    st.markdown('<style>.block-container{padding-top:1.4rem;padding-bottom:1rem}h1{font-size:1.55rem!important}h2{font-size:1.15rem!important}[data-testid="stMetricValue"]{font-size:clamp(1rem,1.7vw,1.6rem)}[data-testid="stMetricValue"]>div,[data-testid="stMetricLabel"] p{white-space:normal!important;overflow:visible!important;text-overflow:clip!important}[data-testid="stSidebar"]{border-right:1px solid #dce1de}@media(max-width:1100px){[data-testid="stHorizontalBlock"]{flex-wrap:wrap}[data-testid="stHorizontalBlock"]>[data-testid="stColumn"]{min-width:200px;flex:1 1 200px}}</style>',unsafe_allow_html=True)
    if 'map_pending' in st.session_state:st.session_state['f_district']=[st.session_state.pop('map_pending')]
    st.sidebar.image(str(ROOT/'assets'/'grupa93-logo.png'),width=80)
    primary_panel=st.sidebar.container(key='primary_filters')
    st.sidebar.markdown('Nekustamā īpašuma darījumi')
    page=st.session_state.get('page','Zeme')
    if page not in PAGES:
        select_page('Zeme')
        page='Zeme'
    st.sidebar.button('Darījumu pārskats',key='nav_overview',width='stretch',type='primary' if page in CATEGORIES else 'secondary',on_click=select_page,args=('Zeme',))
    for section in PAGES[3:]:
        st.sidebar.button(section,key='nav_'+section,width='stretch',type='primary' if page==section else 'secondary',on_click=select_page,args=(section,))
    st.markdown('''<style>
    /* Plotly PNG download: downward arrow instead of the camera icon. */
    .js-plotly-plot .modebar-btn[data-title="Download plot as a png" i] svg path,
    .js-plotly-plot .modebar-btn[aria-label="Download plot as a png" i] svg path {
        d: path("M 440 80 L 560 80 L 560 490 L 730 320 L 815 405 L 500 720 L 185 405 L 270 320 L 440 490 Z M 140 700 L 260 700 L 260 820 L 740 820 L 740 700 L 860 700 L 860 940 L 140 940 Z");
        transform: none;
    }

    .st-key-market_navigation{max-width:960px;margin:0 auto 1.5rem auto}
    [data-testid="stMainBlockContainer"]{padding-top:3rem!important}
    .st-key-market_navigation{scroll-margin-top:3rem}
    .st-key-building_type_panel{max-width:600px;margin:0 auto .5rem auto}
    .st-key-built_price_control{background:#e8f1ec;border:1px solid #9ab9aa!important;border-left:5px solid #285b52!important;border-radius:10px;padding:12px 18px;margin-top:12px}
    .st-key-built_price_control [data-testid="stWidgetLabel"] p{font-size:1.1rem!important;font-weight:700;color:#234f43}
    .st-key-market_navigation button{min-height:64px;border-radius:12px}
    .st-key-market_navigation button p{font-size:1.3rem!important;font-weight:600}
    .st-key-territory_section,.st-key-overview_section{padding:20px;border-radius:14px!important;margin-bottom:32px;border-top:5px solid #285b52!important}
    .st-key-overview_section{border-top-color:#9b7650!important}
    .section-heading{display:flex;align-items:center;gap:14px;background:#e8f1ec;padding:16px 20px;border-radius:8px;margin-bottom:8px}
    .st-key-overview_section .section-heading{background:#f3eee6}
    .section-heading span{font-size:1rem;font-weight:700;color:#52675d}
    .section-heading h2{font-size:1.65rem!important;margin:0!important;padding:0!important}
    @media(max-width:700px){.st-key-territory_section,.st-key-overview_section{padding:10px}.section-heading{padding:12px}.section-heading h2{font-size:1.3rem!important}}
    .st-key-primary_filters{background:#e8f1ec;border:1px solid #9ab9aa;border-radius:12px;padding:14px;margin-bottom:18px}
    .st-key-primary_filters [data-testid="stWidgetLabel"] p{font-size:1rem;font-weight:600;color:#234f43}
    .st-key-primary_filters [data-baseweb="select"]>div{min-height:46px;background:white}
    .district-selection-hint{background:#fff8e9;border:2px solid #9b7650;border-left:5px solid #9b7650;border-radius:10px;padding:12px 16px;color:#654629;font-weight:600}
    .st-key-district_choice{background:#fff8e9;border:2px solid #9b7650;border-left:5px solid #9b7650;border-radius:10px;padding:12px;margin-bottom:8px}
    .st-key-district_choice [data-testid="stWidgetLabel"] p{font-size:1.05rem!important;font-weight:700!important;color:#654629!important}
    .st-key-district_choice [data-baseweb="select"]>div{border-color:#9b7650}
    .st-key-primary_filters h3{font-size:1.15rem!important;color:#234f43}
    .st-key-compact_local_filters [data-testid="stWidgetLabel"] p{font-size:.85rem!important;font-weight:500}
    .st-key-compact_local_filters [data-baseweb="select"]>div{min-height:34px;padding-top:0;padding-bottom:0;font-size:.85rem}
    .st-key-compact_local_filters [data-baseweb="tag"]{font-size:.8rem;margin-top:2px;margin-bottom:2px}
    .st-key-compact_local_filters [data-testid="stVerticalBlock"]{gap:.55rem}
    .st-key-compact_date_filters [data-testid="stMarkdownContainer"] p{font-size:.85rem!important}
    .st-key-compact_date_filters [data-testid="stWidgetLabel"] p{font-size:.85rem!important;font-weight:500}
    .st-key-compact_date_filters [data-baseweb="input"]{min-height:34px}
    .st-key-compact_date_filters input{font-size:.85rem!important;padding-top:5px!important;padding-bottom:5px!important;min-height:0}
    .st-key-compact_date_filters [data-testid="stVerticalBlock"]{gap:.15rem!important}
    .st-key-compact_date_filters [data-testid="stDateInput"]{margin-bottom:0}
    .st-key-nilm_filter p{font-size:.85rem;white-space:normal;overflow-wrap:anywhere}
    [data-testid="stSidebarUserContent"]{padding-top:0;margin-top:-1.5rem}
    </style>''',unsafe_allow_html=True)
    with st.container(key='market_navigation'):
        for column,category in zip(st.columns(3),CATEGORIES):
            column.button(category,key='nav_'+category,width='stretch',type='primary' if page==category else 'secondary',on_click=select_page,args=(category,))
    building_panel=st.container(key='building_type_panel') if page=='Zeme ar ēkām' else None
    state=current();st.title(page)
    status_path=DATA/'status.json'
    status=json.loads(status_path.read_text(encoding='utf8')) if status_path.exists() else {}
    if status.get('status')=='running':st.info('Notiek datu atjaunošana. Analītikā saglabāta iepriekšējā pārbaudītā versija.')
    if status.get('status')=='failed':st.warning('Atjaunošana neizdevās vai avots nav pieejams. Tiek izmantota iepriekšējā darba datubāze, ja tāda ir.')
    if not state:st.info('Dati vēl nav ielādēti. Palaidiet scripts/Update.ps1.');return
    if state.get('mode')!='official':st.warning('Demonstrācijas dati')
    st.caption('VZD · Atjaunots '+state['updated_at'][:10]+' · Darījumi no 2012. gada')
    if page=='Metodika':st.markdown((ROOT/'METHODOLOGY_SHORT_LV.md').read_text(encoding='utf8'));return
    quality=json.loads((ROOT/state['report']/'quality.json').read_text(encoding='utf8'))
    if page=='Datu kvalitāte':
        a,b,c,d=st.columns(4)
        for col,label,value in zip([a,b,c,d],['Resursi','Avota rindas','Unikāli darījumi','Nederīgi darījumi'],[quality['resources'],quality['raw_rows'],quality['transactions'],quality['invalid_transactions']]):col.metric(label,fmt(value))
        st.write('Pieejamais darījumu datumu periods:', ' — '.join(quality['period']))
        st.write('Pēdējās atjaunošanas statuss:',status.get('status','nav datu'))
        if status.get('error'):st.error('Avota vai apstrādes pārbaude neizdevās. Tehniskais apraksts ir logs/ un atjaunošanas pārskatā.')
        st.dataframe(present(pd.DataFrame(quality['profiles'])),hide_index=True,width='stretch');st.dataframe(present(pd.DataFrame(quality['flag_counts'])),hide_index=True,width='stretch')
        st.write('Versiju salīdzinājums',quality.get('changes',{}))
        if quality.get('territory_methods'):
            st.write('Teritoriju piesaistes pārklājums')
            st.dataframe(pd.DataFrame(quality['territory_methods']).rename(columns={'territory_method':'Piesaistes metode','n':'Darījumu skaits'}),hide_index=True)
            st.caption('Ciems noteikts '+fmt(quality['with_village'])+' darījumiem. Koordinātas pieejamas '+fmt(quality['coordinate_transactions'])+' darījumiem.')
        st.download_button('Lejupielādēt kvalitātes pārskatu',json.dumps(quality,ensure_ascii=False,indent=2),'quality.json')
        st.download_button('Lejupielādēt ID izsekošanas piemērus',(ROOT/state['report']/'traces.json').read_bytes(),'traces.json')
        with st.expander('Atkārtojumu sadalījums'):st.dataframe(present(pd.DataFrame(quality['distribution'])),hide_index=True)
        return
    frame=load(str(DATA/state['database']),state['run_id'])
    if page in CATEGORIES:analysis(frame,page,state,primary_panel,building_panel)
    elif page=='Darījumi':
        category=st.selectbox('Kategorija',['Visas']+CATEGORIES+['Neklasificēts'])
        f=filters(frame,None if category=='Visas' else category,primary_panel);st.caption(f'{len(f):,} darījumi atlasē. Nederīgie ieraksti saglabāti ar pazīmēm.');table(f,state)
    else:
        category=st.selectbox('Salīdzināma kategorija',CATEGORIES)
        f=filters(frame,category,primary_panel,comparison=True)
        level=st.radio('Ko salīdzināt?', ['Novadi / valstspilsētas','Pagasti / pilsētas'],horizontal=True,key='compare_level')
        f=f[f.valid].copy()
        if level=='Pagasti / pilsētas':
            parish=f.parish.fillna('').str.strip()
            city=f.city.fillna('').str.strip()
            territory=parish.where(parish.ne(''),city)
            f=f[territory.ne('')].copy()
            f['comparison_territory']=territory.loc[f.index]+' · '+f.district.fillna('')
            selection_label='Izvēlies pagastus / pilsētas'
            selection_key='compare_local_territories'
        else:
            f['comparison_territory']=f.district
            selection_label='Izvēlies novadus / valstspilsētas'
            selection_key='compare_districts'
        options=sorted(f.comparison_territory.dropna().unique())
        if selection_key in st.session_state:
            st.session_state[selection_key]=[v for v in st.session_state[selection_key] if v in options]
        selected=st.multiselect(selection_label,options,default=options[:3] if selection_key not in st.session_state else None,key=selection_key,placeholder='Meklē pēc nosaukuma un izvēlies vairākas teritorijas')
        f=f[f.comparison_territory.isin(selected)]
        if f.empty:st.info('Izvēlieties teritorijas ar darījumiem.');return
        comparison_labels={**{k:price_label(v) for k,v in LABELS.items()},'comparison_territory':'Teritorija'}
        result=aggregate(f,['comparison_territory']).drop(columns=['weighted_price'],errors='ignore')
        available=['count','sum','land_area','building_area','mean_price'] if category=='Zeme ar ēkām' else ['count','sum','area','median_price','mean_price']
        yearly=aggregate(f,['year','comparison_territory']);metric=st.selectbox('Rādītājs',available,format_func=lambda value:price_label(LABELS.get(value,value)))
        chart(px.line(yearly,x='year',y=metric,color='comparison_territory',markers=True,labels=comparison_labels))
        st.dataframe(present(result).rename(columns={'comparison_territory':'Teritorija'}),hide_index=True,width='stretch')
        st.download_button('Eksportēt salīdzinājumu CSV',export_csv(result),'teritorijas.csv')

