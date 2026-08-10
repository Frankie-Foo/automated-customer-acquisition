import { useMemo, useState } from "react";
import { api } from "./api.js";

const emptyPlan = {
  name: "",
  owner_user_id: "",
  regions: "",
  industries: "",
  company_types: "distributor, retailer",
  role_terms: "founder, owner, business development director",
  daily_lead_limit: 20,
  combinations_per_run: 3,
};

export default function AcquisitionControl({ users, plans, flywheel, onRefresh }) {
  const [form, setForm] = useState(emptyPlan);
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState("");
  const salesUsers = useMemo(() => users.filter((item) => item.active && item.role === "sales"), [users]);

  async function createPlan() {
    if (!form.name.trim() || !form.owner_user_id || !form.regions.trim() || !form.industries.trim()) {
      setNotice("请填写计划名称、负责人、地区和行业。");
      return;
    }
    await run("create", async () => {
      await api("/api/admin/acquisition-plans", {
        method: "POST",
        body: JSON.stringify({
          ...form,
          owner_user_id: Number(form.owner_user_id),
          regions: splitList(form.regions),
          industries: splitList(form.industries),
          company_types: splitList(form.company_types),
          role_terms: splitList(form.role_terms),
          daily_lead_limit: Number(form.daily_lead_limit),
          combinations_per_run: Number(form.combinations_per_run),
        }),
      });
      setForm(emptyPlan);
      setNotice("获客计划已创建，将按计划周期自动执行。");
    });
  }

  async function runPlans() {
    await run("plans", async () => {
      const result = await api("/api/admin/acquisition-plans/run", { method: "POST", body: "{}" });
      setNotice(`本次执行 ${Number(result.plans || 0)} 个到期计划，完成 ${Number(result.completed || 0)} 个，入库 ${Number(result.promoted || 0)} 条。`);
    });
  }

  async function runFlywheel() {
    await run("flywheel", async () => {
      const result = await api("/api/flywheel/run", {
        method: "POST",
        body: JSON.stringify({ window_days: 30, min_samples: 5 }),
      });
      const applied = result.learning?.applied?.length || 0;
      setNotice(applied ? `学习完成，应用 ${applied} 项有证据的调整。` : "学习完成，当前继续收集样本，未自动改规则。");
    });
  }

  async function run(key, operation) {
    setBusy(key);
    setNotice("");
    try {
      await operation();
      await onRefresh();
    } catch (error) {
      setNotice(error.message);
    } finally {
      setBusy("");
    }
  }

  return (
    <section className="admin-card acquisition-control">
      <div className="card-title-row">
        <div><h3>自动获客计划</h3><p className="muted">后台按地区、行业和客户类型组合搜索，结果归属指定销售。</p></div>
        <button type="button" disabled={!!busy} onClick={runPlans}>{busy === "plans" ? "执行中..." : "执行到期计划"}</button>
      </div>
      {notice && <div className="admin-alert">{notice}</div>}
      <div className="form-grid acquisition-plan-form">
        <label>计划名称<input value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} placeholder="英国奢侈品经销商" /></label>
        <label>负责人<select value={form.owner_user_id} onChange={(event) => setForm({ ...form, owner_user_id: event.target.value })}><option value="">选择销售</option>{salesUsers.map((item) => <option key={item.id} value={item.id}>{item.display_name || item.username}</option>)}</select></label>
        <label>地区<input value={form.regions} onChange={(event) => setForm({ ...form, regions: event.target.value })} placeholder="United Kingdom, London" /></label>
        <label>行业<input value={form.industries} onChange={(event) => setForm({ ...form, industries: event.target.value })} placeholder="luxury, watch, premium retail" /></label>
        <label>公司类型<input value={form.company_types} onChange={(event) => setForm({ ...form, company_types: event.target.value })} /></label>
        <label>目标职位<input value={form.role_terms} onChange={(event) => setForm({ ...form, role_terms: event.target.value })} /></label>
        <label>每日线索上限<input type="number" min="1" max="1000" value={form.daily_lead_limit} onChange={(event) => setForm({ ...form, daily_lead_limit: event.target.value })} /></label>
        <label>每轮组合数<input type="number" min="1" max="50" value={form.combinations_per_run} onChange={(event) => setForm({ ...form, combinations_per_run: event.target.value })} /></label>
      </div>
      <div className="panel-actions"><button className="primary" type="button" disabled={!!busy} onClick={createPlan}>{busy === "create" ? "创建中..." : "创建计划"}</button></div>
      <PlanTable plans={plans} />
      <div className="card-title-row flywheel-head">
        <div><h3>自动学习飞轮</h3><p className="muted">只用真实回复、会议、成交、退信和退订校准策略；样本不足时不自动修改。</p></div>
        <button type="button" disabled={!!busy} onClick={runFlywheel}>{busy === "flywheel" ? "学习中..." : "刷新学习策略"}</button>
      </div>
      <FlywheelSummary data={flywheel} />
    </section>
  );
}

function PlanTable({ plans }) {
  if (!plans.length) return <div className="empty-state">暂无自动获客计划。</div>;
  return <div className="table-shell"><table className="admin-data-table compact-table"><thead><tr><th>计划</th><th>负责人</th><th>范围</th><th>上限</th><th>最近执行</th><th>状态</th></tr></thead><tbody>{plans.map((plan) => <tr key={plan.id}><td><strong>{plan.name}</strong></td><td>{plan.display_name || plan.username}</td><td>{[...(plan.regions || []), ...(plan.industries || [])].join(" · ") || "--"}</td><td>{plan.daily_lead_limit}/天</td><td>{plan.last_run_completed_at ? formatDate(plan.last_run_completed_at) : "尚未执行"}</td><td><span className={`status-pill ${plan.status === "active" ? "is-active" : "is-paused"}`}>{plan.status === "active" ? "运行中" : plan.status}</span></td></tr>)}</tbody></table></div>;
}

function FlywheelSummary({ data }) {
  const snapshots = data?.snapshots || [];
  const events = data?.learning_events || [];
  if (!snapshots.length && !events.length) return <div className="empty-state">尚无足够触达结果，系统会继续收集真实样本。</div>;
  return <div className="flywheel-summary"><div className="flywheel-snapshots">{snapshots.slice(0, 6).map((item) => { const metrics = item.metrics || {}; return <article key={`${item.scope_type}:${item.scope_key}`}><span>{item.scope_type === "global" ? "全局" : item.scope_key}</span><b>{Number(metrics.replied || 0)} 回复 / {Number(metrics.sent || 0)} 发送</b><small>正向 {percent(metrics.positive_reply_rate)} · 退信 {percent(metrics.bounce_rate)}</small></article>; })}</div>{events.length > 0 && <p className="muted">最近自动学习：{events[0].reason || events[0].action_type}（{formatDate(events[0].created_at)}）</p>}</div>;
}

function splitList(value) {
  return String(value || "").split(/[,，\n]/).map((item) => item.trim()).filter(Boolean);
}

function percent(value) {
  return `${(Number(value || 0) * 100).toFixed(1)}%`;
}

function formatDate(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "--" : date.toLocaleString("zh-CN", { hour12: false });
}
