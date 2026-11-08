import json, os, sys
ROOT=os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
p=json.load(open(os.path.join(ROOT,'portal','pick_20261106.json'),encoding='utf-8'))
d={r['code']:r for r in p['all']}
bad=0
def chk(cond,msg):
    global bad
    print(('  OK  ' if cond else '  FAIL')+' '+msg)
    if not cond: bad+=1

print("A1 破位股必须被 J 维识别（金山办公信号后最深 -10.6%）")
r=d.get('688111.SH')
chk(r is not None and r['break_pct'] < -8, "金山办公 break_pct=%s < -8"%(r['break_pct'] if r else 'NA'))
chk(r is not None and r['dims']['J'] <= 0.15, "金山办公 J=%s 应为最低档"%(r['dims']['J'] if r else 'NA'))

print("A2 未破位股 J 应为满分（仙琚制药 brk=0）")
r2=d.get('002332.SZ')
chk(r2 is not None and r2['dims']['J'] == 1.0, "仙琚制药 J=%s"%(r2['dims']['J'] if r2 else 'NA'))

print("A3 zone 与 dzg 符号必须自洽（三买 zone=above 时 dzg 应 >0）")
incons=[x for x in p['all'] if x['type']=='3买' and x['zone']=='above' and (x['dzg_pct'] or 0)<=0]
chk(len(incons)==0, "不自洽行数 %d"%len(incons))

print("A4 已被最终清单选中的，必须同时满足 brk>=-8 与 zone!=below（2/3买）")
picks=p['picks']
viol=[x for x in picks if (x['break_pct'] or 0)<-8
      or (x['type'] in ('2买','3买') and x['zone']=='below')]
chk(len(viol)==0, "违例 %d 只"%len(viol))

print("A5 RR 与 stop/up 必须自洽（|rr - up/stop| < 0.05）")
err=0
for x in p['all']:
    if x['rr'] and x['stop_pct'] and x['up_pct']:
        if abs(x['rr'] - x['up_pct']/x['stop_pct']) > 0.05: err+=1
chk(err==0, "不自洽 %d 只"%err)

print("\n%s"%("全部通过" if bad==0 else "失败 %d 项"%bad))
sys.exit(0 if bad==0 else 1)
