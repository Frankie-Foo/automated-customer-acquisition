import { useEffect, useState } from "react";
import { api } from "./api.js";

export default function EmailPerformance() {
  const [days, setDays] = useState(14);
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const [expanded, setExpanded] = useState(false);
  useEffect(() => {
    let current = true;
    setData(null);
    setError("");
    setExpanded(false);
    api(`/api/email-performance?days=${days}`).then(value => {
      if (current) setData(value);
    }).catch(err => { if (current) setError(err.message); });
    return () => { current = false; };
  }, [days, revision]);
  useEffect(() => {
    const refresh = () => setRevision(value => value + 1);
    window.addEventListener("salesbot:refresh-related", refresh);
    window.addEventListener("salesbot:contacts-refresh", refresh);
    return () => {
      window.removeEventListener("salesbot:refresh-related", refresh);
      window.removeEventListener("salesbot:contacts-refresh", refresh);
    };
  }, []);
  return <section className="email-performance" aria-label="邮件效果复盘">
    <header className="section-head"><div><h2>邮件效果与下一步</h2><span className="muted">最近90天 · 按发送周 · 北京时间{data ? ` · ${data.scope === "all" ? "全体客户" : "我当前负责的客户"}` : ""}</span></div>
      <label>观察期 <select aria-label="邮件效果观察期" value={days} onChange={event => setDays(Number(event.target.value))}>
        {[7, 14, 30].map(value => <option key={value} value={value}>{value}天</option>)}
      </select></label></header>
    {error ? <div role="alert">{error} <button type="button" onClick={() => setRevision(value => value + 1)}>重试</button></div>
      : !data ? <p role="status">正在统计邮件效果...</p>
      : !data.weeks?.length ? <p>最近90天暂无真实发送记录。</p>
      : <div className="performance-weeks">{(expanded ? data.weeks : data.weeks.slice(0, 3)).map(row => {
        const waiting = row.sent - row.matured;
        const risk = row.matured >= 20 && (row.bounced / row.matured > .05 || row.unsubscribed / row.matured > .01);
        const conclusion = !row.matured ? "观察中" : risk ? "需排查" : row.positive ? "已有正向回复" : "暂无正向回复证据";
        const action = !row.matured ? "等待观察期结束，先处理已有客户回复。" : risk ? "先核查退信、退订及名单质量，暂勿扩量。"
          : row.positive ? "跟进正向客户，记录需求及下一步；下批保留对照，不直接全量复制。"
          : "先查送达与目标客户匹配，再对照测试一个合作切入点。";
        const percent = value => row.matured ? `${(100 * value / row.matured).toFixed(1)}%` : "待观察";
        return <article key={row.week} className={`performance-week ${risk ? "is-risk" : ""}`}>
          <header><strong>{String(row.week).slice(0, 10)} 当周</strong><span>{conclusion}</span></header>
          <dl>{[["发送", row.sent], ["已满观察期", row.matured], ["观察中", waiting],
            ["人工回复", row.matured ? `${row.replied} · ${percent(row.replied)}` : "待观察"], ["正向回复", row.matured ? `${row.positive} · ${percent(row.positive)}` : "待观察"],
            ["退信 / 退订", row.matured ? `${row.bounced} / ${row.unsubscribed}` : "待观察"]].map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>
          <p><strong>下一步：</strong>{action}</p>
          <a href="#followup">处理客户跟进</a>
        </article>;
      })}</div>}
    {data?.weeks?.length > 3 && <button type="button" aria-expanded={expanded} onClick={() => setExpanded(value => !value)}>{expanded ? "收起历史周" : `查看全部 ${data.weeks.length} 周`}</button>}
    <small className="muted">回复率分母为已满观察期的邮件；只计观察期内可归因的人工回复。历史未归因回复不计入，零值不等于没有客户回复。周汇总不是对照实验结论。</small>
  </section>;
}
