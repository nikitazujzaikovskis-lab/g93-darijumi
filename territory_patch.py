"""Apply the audited territory-only correction to the bundled public snapshot."""
import hashlib,json,shutil
from pathlib import Path
import duckdb,pandas as pd

def digest(path):
    with path.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()

def write_json(path,value):
    temp=path.with_suffix(path.suffix+'.tmp')
    temp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf8')
    temp.replace(path)

def install(root,payload_path):
    root=Path(root);payload_path=Path(payload_path)
    patch=json.loads(payload_path.read_text(encoding='utf8'));version=digest(payload_path)
    pointer=root/'data/processed/current.json'
    state=json.loads(pointer.read_text(encoding='utf8'))
    if state.get('territory_patch')==version:return
    old=root/'data'/state['database'].replace('\\','/')
    if digest(old)!=patch['base_sha256']:raise ValueError('Unexpected base database for territory correction')
    fields=patch['fields'];rows=patch['patches']
    target=root/'data/processed'/(patch['run_id']+'.duckdb')
    temp=target.with_suffix('.preparing.duckdb')
    shutil.copyfile(old,temp)
    with duckdb.connect(str(temp),config={'memory_limit':'256MB','threads':'2'}) as c:
        before=c.execute('SELECT transaction_id,'+','.join(fields)+' FROM transactions WHERE transaction_id IN (SELECT unnest(?))',[[p['transaction_id'] for p in rows]]).df().set_index('transaction_id').to_dict('index')
        if len(before)!=len(rows) or any(before.get(p['transaction_id'])!=p['before'] for p in rows):raise ValueError('Territory patch precondition mismatch')
        c.execute('BEGIN TRANSACTION')
        c.register('patch_rows',pd.DataFrame([{'transaction_id':p['transaction_id'],**p['after']} for p in rows]))
        c.execute('UPDATE transactions SET '+','.join(f'{f}=r.{f}' for f in fields)+' FROM patch_rows r WHERE transactions.transaction_id=r.transaction_id')
        c.execute("DELETE FROM quality_flags WHERE rule='territory_unmatched' AND transaction_id IN (SELECT transaction_id FROM patch_rows)")
        c.execute('CREATE TABLE territory_reform_audit(transaction_id VARCHAR,before_json VARCHAR,after_json VARCHAR,evidence_json VARCHAR)')
        c.register('audit_rows',pd.DataFrame([dict(transaction_id=p['transaction_id'],before_json=json.dumps(p['before'],ensure_ascii=False),after_json=json.dumps(p['after'],ensure_ascii=False),evidence_json=json.dumps(p['evidence'],ensure_ascii=False)) for p in rows]))
        c.execute('INSERT INTO territory_reform_audit SELECT * FROM audit_rows')
        c.register('history_rows',pd.DataFrame(patch['history']))
        c.execute('CREATE TABLE territory_history AS SELECT * FROM history_rows')
        after=c.execute('SELECT transaction_id,'+','.join(fields)+' FROM transactions WHERE transaction_id IN (SELECT unnest(?))',[[p['transaction_id'] for p in rows]]).df().set_index('transaction_id').to_dict('index')
        assert all(after[p['transaction_id']]==p['after'] for p in rows)
        assert c.execute('SELECT count(*) FROM transactions').fetchone()[0]==patch['total']
        assert c.execute("SELECT count(*) FROM transactions WHERE district_code=''").fetchone()[0]==26
        c.execute('INSERT INTO update_runs VALUES (?,?,?)',[patch['run_id'],patch['updated_at'],'success'])
        c.execute('COMMIT');c.execute('CHECKPOINT')
    temp.replace(target)
    for name,content in patch['code'].items():
        # Only this fixed set of application modules may be replaced.
        assert name in ('source.py','territory.py','territory_recovery.py','reform_recovery.py')
        (root/'g93'/name).write_text(content,encoding='utf8')
    report=root/'reports/update_runs'/patch['run_id'];report.mkdir(parents=True,exist_ok=True)
    shutil.copyfile(root/state['report'].replace('\\','/')/'traces.json',report/'traces.json')
    write_json(report/'quality.json',patch['quality'])
    write_json(report/'territory_reform.json',{'patches':rows})
    result={**state,'run_id':patch['run_id'],'updated_at':patch['updated_at'],'database':target.relative_to(root/'data').as_posix(),'database_sha256':digest(target),'report':report.relative_to(root).as_posix(),'territory_patch':version}
    write_json(pointer,result)
    write_json(root/'data/status.json',result)
