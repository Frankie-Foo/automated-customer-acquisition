const o={status:"",filter:"",search:"",user:null,usage:null,linkedinTaskId:null},S=document.querySelector("#notice"),de=document.querySelector("#metrics"),R=document.querySelector("#ops-report-content"),ce=document.querySelector("#admin-console"),U=document.querySelector("#admin-summary"),g=document.querySelector("#admin-users-table"),h=document.querySelector("#admin-senders-table"),le=document.querySelector("#followup-grid"),ue=document.querySelector("#lifecycle-grid"),me=document.querySelector("#workspace-empty"),X=document.querySelector("#workspace-content"),pe=document.querySelector("#workspace-profile"),I=document.querySelector("#activity-list"),Y=document.querySelector("#stage-analysis"),O=document.querySelector("#contacts-body"),D=document.querySelector("#readiness"),$=document.querySelector("#ready-pill"),j=document.querySelector("#login-screen"),A=document.querySelector("#login-form"),C=document.querySelector("#login-error"),x=document.querySelector("#password-change-form"),L=document.querySelector("#password-change-error"),B=document.querySelector("#account-name"),F=document.querySelector("#quota-status"),ye=document.querySelector("#logout-button"),k=document.querySelector("#linkedin-search-output");function s(e,t=""){S.textContent=e,S.className=`notice ${t}`.trim(),S.scrollIntoView({behavior:"smooth",block:"nearest"})}function fe(){S.className="notice hidden"}async function c(e,t={}){const n=await fetch(e,{headers:{"Content-Type":"application/json"},...t}),a=await n.json();if(n.status===401&&e!=="/api/login")throw M(),new Error("请先登录");if(!a.ok)throw new Error(a.error||"请求失败");return a.data}async function ge(){try{const e=await c("/api/me");if(o.user=e.user,o.usage=e.usage,_(),o.user.must_change_password){Z();return}H(),await u()}catch{M()}}function M(){j.classList.remove("hidden"),A.classList.remove("hidden"),x.classList.add("hidden")}function H(){j.classList.add("hidden"),C.textContent="",L.textContent=""}function Z(){j.classList.remove("hidden"),A.classList.add("hidden"),x.classList.remove("hidden"),L.textContent=""}function _(){if(!o.user){B.textContent="未登录",F.textContent="今日配额 --",V();return}const e=o.usage||{};B.textContent=o.user.display_name||o.user.username,F.textContent=`获客 ${e.source_count||0}/${o.user.daily_source_limit} · 发信 ${e.send_count||0}/${o.user.daily_send_limit}`,ce.classList.toggle("hidden",o.user.role!=="admin"),V()}function T(e){e&&(o.usage=e,_())}function V(){window.dispatchEvent(new CustomEvent("salesbot:session",{detail:{user:o.user,usage:o.usage}}))}async function u(){try{const[e,t,n]=await Promise.all([c("/api/summary"),c(`/api/contacts?status=${encodeURIComponent(o.status)}&filter=${encodeURIComponent(o.filter)}&search=${encodeURIComponent(o.search)}&limit=100`),c("/api/lifecycle")]);fe(),W(e);const a=t.contacts||[];window.latestContacts=a,G(a),z(n,a),K(a),await N(),await q(),await re(),he()}catch(e){W({total_contacts:0,sent_today:0,statuses:{},events_7d:{}}),G([]),z({stages:{}},[]),K([]),P({}),s(`数据库还不可用：${e.message}。先确认 .env/config.yaml，然后点“初始化/迁移数据库”。`,"error")}}async function N(){try{P(await c("/api/ops-report"))}catch{P({})}}window.addEventListener("salesbot:ops-refresh",N);async function q(){var e;if(!window.SALESBOT_REACT_ADMIN&&((e=o.user)==null?void 0:e.role)==="admin")try{const[t,n]=await Promise.all([c("/api/admin/users"),c("/api/admin/senders")]);be(t.users||[],n.senders||[]),we(t.users||[]),$e(n.senders||[])}catch(t){g.innerHTML=`<div class="empty-state">管理员数据加载失败：${i(t.message)}</div>`}}async function he(){try{const e=await c("/api/readiness");$.textContent=e.ready?"Ready":"Action needed",$.className=e.ready?"ready":"missing",D.innerHTML=e.checks.map(t=>`
      <div class="check ${t.ok?"ok":"missing"}" title="${i(t.message||"")}">
        <span>${i(ve(t.name))}</span>
        <strong>${t.ok?"OK":t.required?"缺失":"可选"}</strong>
      </div>
    `).join("")}catch{$.textContent="Error",$.className="missing",D.innerHTML='<div class="check missing"><span>readiness</span><strong>失败</strong></div>'}}function W(e){var a,r;const t=e.events_7d||{},n=[["客户总数",e.total_contacts||0,"系统内全部客户"],["今日发送",e.sent_today||0,"当天真实/演练发送"],["待发送",((a=e.statuses)==null?void 0:a.queued)||0,"已入队等待触达"],["7天打开",t.opened||0,"最近 7 天打开事件"],["已回复",((r=e.statuses)==null?void 0:r.replied)||0,"需要销售跟进"]];de.innerHTML=n.map(([d,p,m])=>`
    <div class="metric">
      <span>${d}</span>
      <strong>${p}</strong>
      <small>${m}</small>
    </div>
  `).join("")}function ve(e){return{database:"数据库连接",lead_source:"自动获客 API",enrichment:"邮箱富化 API",resend:"邮件发送 API",sender_email:"发件邮箱域名",dry_run:"真实发送开关",public_url:"公网访问地址",admin_password:"管理员密码",social_enrichment:"社媒富化 API",llm:"AI 文案模型",slack:"Slack 通知"}[e]||e}function G(e){const t=e.filter(d=>Number(d.opened_count||0)>0&&!["replied","bounced","unsubscribed"].includes(d.status)),n=e.filter(d=>d.status==="replied"||Number(d.replied_count||0)>0),a=e.filter(d=>d.status==="bounced"||Number(d.bounced_count||0)>0),r=[{title:"已打开未回复",count:t.length,hint:"建议今天人工跟进或准备下一封",tone:"hot",contacts:t},{title:"已回复",count:n.length,hint:"需要销售马上接手沟通",tone:"reply",contacts:n},{title:"退信需处理",count:a.length,hint:"检查邮箱质量或加入黑名单",tone:"risk",contacts:a}];le.innerHTML=r.map(_e).join("")}function _e(e){const t=e.contacts.slice(0,3).map(a=>`
    <li>
      <div>
        <strong>${i(b(a))}</strong>
        <span>${i(a.company_name||a.company_domain||"")}</span>
      </div>
      <em>${i(Se(a))}</em>
    </li>
  `).join("");return`
    <article class="followup-card ${e.tone}">
      <div class="followup-title">
        <div>
          <strong>${i(e.title)}</strong>
          <span>${i(e.hint)}</span>
        </div>
        <b>${e.count}</b>
      </div>
      <ul>${t||'<li class="empty-task">当前没有需要处理的客户</li>'}</ul>
    </article>
  `}function P(e){var m,y;const t=e.totals||{},n=e.events||{},a=(e.scope||"")==="team"||((m=o.user)==null?void 0:m.role)==="admin",r=(e.provider_stats||[]).slice(0,8).map(l=>`
    <tr>
      <td>${i(l.provider)}</td>
      <td>${l.calls||0}</td>
      <td>${l.candidates||0}</td>
      <td>${l.valid_candidates||0}</td>
      <td>${l.selected||0}</td>
      <td>${l.errors||0}</td>
    </tr>
  `).join(""),d=(e.by_user||[]).map(l=>`
    <tr>
      <td>${i(l.display_name||l.username)}</td>
      <td>${l.source_count_today||0}/${l.daily_source_limit}</td>
      <td>${l.send_count_today||0}/${l.daily_send_limit}</td>
      <td>${l.owned_contacts||0}</td>
      <td>${l.active?"启用":"停用"}</td>
    </tr>
  `).join(""),p=(e.failures||[]).slice(0,6).map(l=>`
    <li><span>${i(l.reason)}</span><b>${l.count}</b></li>
  `).join("");R.innerHTML=`
    <div class="ops-cards">
      ${f("今日新增线索",t.new_contacts_today)}
      ${f("今日有效邮箱",t.valid_emails_today)}
      ${f("今日发送",n.sent_today)}
      ${f("今日打开",n.opened_today)}
      ${f("今日回复",(t.replied||0)+(n.replied_events_today||0))}
      ${f("今日退信",(t.bounced||0)+(n.bounced_events_today||0))}
      ${f("今日需处理",(n.opened_no_reply||0)+(t.replied||0)+(t.bounced||0))}
    </div>
    <div class="ops-grid">
      <section>
        <h3>销售配额日报</h3>
        <table class="mini-table"><thead><tr><th>销售</th><th>获客</th><th>发信</th><th>客户</th><th>状态</th></tr></thead><tbody>${d||"<tr><td colspan='5'>暂无数据</td></tr>"}</tbody></table>
      </section>
      <section>
        <h3>邮箱 Provider 统计</h3>
        <table class="mini-table"><thead><tr><th>Provider</th><th>调用</th><th>候选</th><th>Valid</th><th>选中</th><th>错误</th></tr></thead><tbody>${r||"<tr><td colspan='6'>暂无数据</td></tr>"}</tbody></table>
      </section>
      <section>
        <h3>失败原因</h3>
        <ul class="failure-list">${p||"<li><span>暂无失败</span><b>0</b></li>"}</ul>
      </section>
    </div>
  `,a||(y=R.querySelectorAll(".ops-grid section")[1])==null||y.remove()}function f(e,t){return`<article><span>${i(e)}</span><strong>${Number(t||0)}</strong></article>`}function be(e,t){if(!U)return;const n=e.filter(m=>m.active).length,a=e.filter(m=>m.must_change_password).length,r=e.reduce((m,y)=>m+Number(y.source_count_today||0),0),d=e.reduce((m,y)=>m+Number(y.send_count_today||0),0),p=t.filter(m=>m.active).length;U.innerHTML=`
    <article>
      <span>销售账号</span>
      <strong>${n}/${e.length}</strong>
      <small>启用 / 全部</small>
    </article>
    <article>
      <span>今日获客</span>
      <strong>${r}</strong>
      <small>全员已使用</small>
    </article>
    <article>
      <span>今日发信</span>
      <strong>${d}</strong>
      <small>全员已发送</small>
    </article>
    <article>
      <span>发件账号</span>
      <strong>${p}/${t.length}</strong>
      <small>启用 / 全部</small>
    </article>
    <article>
      <span>待改密码</span>
      <strong>${a}</strong>
      <small>首次登录未完成</small>
    </article>
  `}function we(e){if(!e.length){g.innerHTML='<div class="empty-state">暂无用户</div>';return}g.innerHTML=`
    <table class="mini-table admin-data-table">
      <thead><tr><th>成员</th><th>角色</th><th>今日获客</th><th>今日发信</th><th>状态</th><th>配额设置</th><th>操作</th></tr></thead>
      <tbody>
        ${e.map(t=>`
          <tr>
            <td>
              <div class="admin-identity">
                <strong>${i(t.display_name||t.username)}</strong>
                <span>${i(t.username)} · ID ${t.id}</span>
              </div>
            </td>
            <td><span class="role-pill ${t.role==="admin"?"role-admin":""}">${i(t.role)}</span></td>
            <td><strong>${t.source_count_today||0}</strong><span class="muted"> / ${t.daily_source_limit}</span></td>
            <td><strong>${t.send_count_today||0}</strong><span class="muted"> / ${t.daily_send_limit}</span></td>
            <td>
              <span class="status-pill ${t.active?"is-active":"is-paused"}">${t.active?"启用":"停用"}</span>
              ${t.must_change_password?'<span class="status-pill is-warning">待改密码</span>':""}
            </td>
            <td>
              <div class="quota-edit">
                <label>获客<input class="mini-input" data-user-source="${t.id}" type="number" value="${t.daily_source_limit}" /></label>
                <label>发信<input class="mini-input" data-user-send="${t.id}" type="number" value="${t.daily_send_limit}" /></label>
              </div>
            </td>
            <td class="row-actions">
              <button class="primary soft" data-admin-user-save="${t.id}">保存</button>
              <button data-admin-user-toggle="${t.id}" data-active="${t.active?"false":"true"}">${t.active?"停用":"启用"}</button>
              <button data-admin-user-reset="${t.id}">重置密码</button>
            </td>
          </tr>
        `).join("")}
      </tbody>
    </table>
  `}function $e(e){if(!e.length){h.innerHTML='<div class="empty-state">暂无发件账号。先在 config.yaml 的 sender_pool.accounts[] 配置。</div>';return}h.innerHTML=`
    <table class="mini-table admin-data-table">
      <thead><tr><th>发件身份</th><th>Provider</th><th>今日发送</th><th>每日上限</th><th>Warmup</th><th>状态</th><th>操作</th></tr></thead>
      <tbody>
        ${e.map(t=>`
          <tr>
            <td>
              <div class="admin-identity">
                <strong>${i(t.name)}</strong>
                <span>${i(t.email)} · ID ${t.id}</span>
              </div>
            </td>
            <td>${i(t.provider)}</td>
            <td><strong>${t.send_count_today||0}</strong><span class="muted"> / ${t.daily_limit}</span></td>
            <td><input class="mini-input" data-sender-limit="${t.id}" type="number" value="${t.daily_limit}" /></td>
            <td>
              <select class="mini-input" data-sender-warmup="${t.id}">
                <option value="warmup" ${t.warmup_stage==="warmup"?"selected":""}>warmup</option>
                <option value="production" ${t.warmup_stage==="production"?"selected":""}>production</option>
              </select>
            </td>
            <td><span class="status-pill ${t.active?"is-active":"is-paused"}">${t.active?"启用":"停用"}</span></td>
            <td class="row-actions">
              <button class="primary soft" data-admin-sender-save="${t.id}">保存</button>
              <button data-admin-sender-toggle="${t.id}" data-active="${t.active?"false":"true"}">${t.active?"停用":"启用"}</button>
            </td>
          </tr>
        `).join("")}
      </tbody>
    </table>
  `}const ee=[["lead","陌生线索"],["replied","已回复"],["conversation","初步沟通"],["meeting","约会/会议"],["business_plan","商业计划"],["store_visit","到店参观"],["trial_order","试订单"],["agency_agreement","代理协议"],["hq_visit","总部拜访"],["signed","成功签约"],["maintenance","持续维护"],["waiting_pool","等待池"],["abandoned","已放弃"]];function z(e,t){const n=e.stages||{};ue.innerHTML=ee.map(([a,r])=>{const d=n[a]||0,m=t.filter(y=>y.lifecycle_stage===a).slice(0,2).map(y=>`<span>${i(b(y))}</span>`).join("");return`
      <article class="lifecycle-card ${a}">
        <strong>${i(r)}</strong>
        <b>${d}</b>
        <div>${m||"<span>暂无客户</span>"}</div>
      </article>
    `}).join("")}function Se(e){return e.status==="replied"||Number(e.replied_count||0)>0?"已回复":e.status==="bounced"||Number(e.bounced_count||0)>0?"退信":Number(e.opened_count||0)>0?`打开 ${e.opened_count} 次`:se(e.last_event_type||e.status)}function K(e){if(!e.length){O.innerHTML=`
      <tr>
        <td colspan="13">
          <div class="empty-state">
            <strong>还没有客户</strong>
            <div>先用上方“自动获客”、CSV 导入，或手动新增一个联系人。</div>
          </div>
        </td>
      </tr>`;return}O.innerHTML=e.map(t=>`
    <tr>
      <td>${t.id}</td>
      <td>
        <strong>${i(b(t))}</strong>
        <div class="muted">${i(t.job_title||"")}</div>
        ${ke(t)}
      </td>
      <td>
        <strong>${i(t.company_name||"")}</strong>
        <div class="muted">${i(t.company_domain||"")}</div>
      </td>
      <td>
        ${i(Le(t))}
        <div class="muted">${i(Ne(t))}</div>
      </td>
      <td><span class="badge ${i(t.status)}">${i(Te(t.status))}</span></td>
      <td>${t.sequence_step||0}</td>
      <td>${qe(t)}</td>
      <td>${Ce(t)}</td>
      <td>${Ee(t)}</td>
      <td>${Ie(t)}</td>
      <td>${E(t.last_contacted_at)}</td>
      <td>${Oe(t)}</td>
      <td class="error-text" title="${i(t.enrich_error||"")}">${i(t.enrich_error||"")}</td>
    </tr>
  `).join("")}function b(e){return[e.first_name,e.last_name].filter(Boolean).join(" ")||"(No name)"}function ke(e){return te(e.linkedin_url)?`<a class="profile-link" href="${i(e.linkedin_url)}" target="_blank" rel="noopener">LinkedIn</a>`:""}function qe(e){const t=e.social_profiles||{},a=Object.entries({linkedin:"LinkedIn",twitter:"X",github:"GitHub",facebook:"Facebook",website:"Website"}).filter(([r])=>te(t[r])).map(([r,d])=>`<a class="social-link" href="${i(t[r])}" target="_blank" rel="noopener">${d}</a>`).join("");return a?`<div class="social-links">${a}</div>`:e.social_error?`<span class="muted" title="${i(e.social_error)}">未找到</span>`:'<span class="muted">待富化</span>'}function te(e){return/^https?:\/\//i.test(String(e||""))}function Le(e){return!e.email||String(e.email).includes("*")?"待富化":e.email}function Ne(e){const t=[e.email_status||"unknown"];return e.email_source&&t.push(ne(e.email_source)),e.email_confidence!==null&&e.email_confidence!==void 0&&t.push(`${e.email_confidence}%`),t.join(" · ")}function ne(e){return{existing:"已有",public_website:"官网",ninjapear:"NinjaPear",prospeo:"Prospeo",hunter:"Hunter",linkedin_public_search:"LinkedIn 公网搜索",linkedin_public_search_guess:"LinkedIn 推断","linkedin_public_search+hunter_verify":"LinkedIn+Hunter 验证","linkedin_public_search+prospeo":"LinkedIn+Prospeo","pattern_guess+hunter_verify":"推断+验证"}[e]||e}function Te(e){return{new:"新线索",enriched:"已富化",queued:"待发送",sent_1:"已发第 1 封",sent_2:"已发第 2 封",sent_3:"已发第 3 封",replied:"已回复",bounced:"已退信",unsubscribed:"已退订"}[e]||e}function J(e){return Object.fromEntries(ee)[e]||e||"陌生线索"}function ae(e){return{active:"推进中",waiting:"等待",abandoned:"已放弃",won:"已签约",lost:"流失"}[e]||e||"推进中"}function Ee(e){return`
    <div class="lifecycle-cell">
      <span class="stage-pill">${i(J(e.lifecycle_stage))}</span>
      <div class="muted">${i(ae(e.disposition))}</div>
      ${e.next_action_at?`<div class="muted">下次：${E(e.next_action_at)}</div>`:""}
    </div>
  `}function Ie(e){const t=e.profile_insights||{};if(!e.profile_summary&&!Object.keys(t).length)return'<div class="profile-summary muted">待生成画像</div>';const n=Number(t.icp_fit_score??0),a=ie(t.intent_level),r=t.next_action||e.profile_summary||"",d=[...(t.interests||[]).slice(0,2),...(t.pain_points||[]).slice(0,1)].map(p=>`<span>${i(p)}</span>`).join("");return`
    <div class="profile-insights" title="${i(e.profile_summary||"")}">
      <div class="fit-line">
        <b>${n||"--"}</b>
        <span>${i(a)}</span>
      </div>
      <strong>${i(t.persona||e.profile_summary||"客户画像")}</strong>
      <p>${i(r||"暂无下一步建议")}</p>
      ${d?`<div class="insight-tags">${d}</div>`:""}
    </div>
  `}function ie(e){return{high:"高意向",medium:"中意向",low:"低意向",unknown:"待判断"}[e]||"待判断"}function Oe(e){return`
    <div class="row-actions">
      <button data-life-action="enrich-email" data-id="${e.id}">邮箱</button>
      <button data-life-action="enrich-social" data-id="${e.id}">社媒</button>
      <button data-life-action="queue-one" data-id="${e.id}">入队</button>
      <button data-life-action="send-one" data-id="${e.id}">发送</button>
      <button data-life-action="next" data-id="${e.id}">推进</button>
      <button data-life-action="wait" data-id="${e.id}">等待</button>
      <button data-life-action="abandon" data-id="${e.id}">放弃</button>
      <button data-life-action="profile" data-id="${e.id}">画像</button>
      <button data-life-action="detail" data-id="${e.id}">详情</button>
    </div>
  `}function Ce(e){const t=[];if(Number(e.sent_count||0)>0&&t.push(["sent",`已发送 ${e.sent_count}`]),Number(e.opened_count||0)>0&&t.push(["opened",`已打开 ${e.opened_count}`]),Number(e.clicked_count||0)>0&&t.push(["clicked",`已点击 ${e.clicked_count}`]),Number(e.replied_count||0)>0&&t.push(["replied","已回复"]),Number(e.bounced_count||0)>0&&t.push(["bounced","已退信"]),Number(e.unsubscribed_count||0)>0&&t.push(["unsubscribed","已退订"]),!t.length)return'<span class="muted">暂无反馈</span>';const n=t.map(([r,d])=>`<span class="event-chip ${r}">${i(d)}</span>`).join(""),a=e.last_event_type?`<div class="muted">最近：${i(se(e.last_event_type))} ${E(e.last_event_at)}</div>`:"";return`<div class="event-list">${n}${a}</div>`}function se(e){return{sent:"已发送",opened:"已打开",clicked:"已点击",replied:"已回复",bounced:"已退信",unsubscribed:"已退订"}[e]||e}function E(e){return e?new Date(e).toLocaleString():""}function i(e){return String(e??"").replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(">","&gt;").replaceAll('"',"&quot;").replaceAll("'","&#039;")}function Pe(e){try{const t=String(e||"").trim();if(!t)return"";let a=new URL(t.includes("://")?t:`https://${t}`).hostname.toLowerCase();a.startsWith("www.")&&(a=a.slice(4));const r=a.split(".").filter(Boolean);return r.length>2&&(a=r.slice(-2).join(".")),a}catch{return String(e||"").trim().replace(/^https?:\/\//,"").replace(/^www\./,"").split("/")[0]}}async function je(e){s("正在执行，请稍等...");const t=await c(`/api/${e}`,{method:"POST",body:JSON.stringify({limit:100})});T(t.usage),s(`完成：${JSON.stringify(t)}`),await u()}document.querySelectorAll("[data-action]").forEach(e=>{e.addEventListener("click",async()=>{try{await je(e.dataset.action)}catch(t){s(t.message,"error")}})});document.querySelectorAll(".tab").forEach(e=>{e.addEventListener("click",()=>{document.querySelectorAll(".tab").forEach(t=>t.classList.remove("active")),document.querySelectorAll(".tab-panel").forEach(t=>t.classList.remove("active")),e.classList.add("active"),document.querySelector(`#tab-${e.dataset.tab}`).classList.add("active")})});document.querySelector("#refresh-button").addEventListener("click",u);A.addEventListener("submit",async e=>{e.preventDefault(),C.textContent="";try{const t=document.querySelector("#login-password").value,n=await c("/api/login",{method:"POST",body:JSON.stringify({username:document.querySelector("#login-username").value.trim(),password:t})});if(o.user=n.user,o.usage=n.usage,document.querySelector("#current-password").value=t,_(),o.user.must_change_password){Z();return}H(),await u()}catch(t){C.textContent=t.message}});x.addEventListener("submit",async e=>{e.preventDefault(),L.textContent="";try{const t=document.querySelector("#current-password").value,n=document.querySelector("#new-password").value,a=document.querySelector("#confirm-password").value;if(n.length<12)throw new Error("新密码至少 12 位");if(n!==a)throw new Error("两次输入的新密码不一致");const r=await c("/api/change-password",{method:"POST",body:JSON.stringify({current_password:t,new_password:n})});o.user=r.user,document.querySelector("#login-password").value="",document.querySelector("#current-password").value="",document.querySelector("#new-password").value="",document.querySelector("#confirm-password").value="",_(),H(),s("密码已更新"),await u()}catch(t){L.textContent=t.message}});ye.addEventListener("click",async()=>{await fetch("/api/logout"),o.user=null,o.usage=null,_(),M()});document.querySelector("#export-button").addEventListener("click",()=>{const e=new URLSearchParams;o.status&&e.set("status",o.status),window.location.href=`/api/export.csv?${e.toString()}`});document.querySelector("#status-filter").addEventListener("change",e=>{o.status=e.target.value,u()});document.querySelector("#contact-filter").addEventListener("change",e=>{o.filter=e.target.value,u()});document.querySelector("#search-input").addEventListener("input",e=>{o.search=e.target.value,window.clearTimeout(window.searchTimer),window.searchTimer=window.setTimeout(u,250)});var Q;window.SALESBOT_REACT_ADMIN||((Q=document.querySelector("#admin-create-user"))==null||Q.addEventListener("click",async()=>{try{const e={username:document.querySelector("#admin-new-username").value.trim(),display_name:document.querySelector("#admin-new-display").value.trim(),password:document.querySelector("#admin-new-password").value,role:"sales",daily_source_limit:Number(document.querySelector("#admin-new-source-limit").value||100),daily_send_limit:Number(document.querySelector("#admin-new-send-limit").value||100)};if(!e.username||!e.password)throw new Error("账号和密码必填");await c("/api/admin/users",{method:"POST",body:JSON.stringify(e)}),s("销售账号已创建"),document.querySelector("#admin-new-password").value="",await q(),await N()}catch(e){s(e.message,"error")}}),g==null||g.addEventListener("click",async e=>{const t=e.target.closest("[data-admin-user-save]"),n=e.target.closest("[data-admin-user-toggle]"),a=e.target.closest("[data-admin-user-reset]");if(!(!t&&!n&&!a))try{const r=Number((t||n||a).dataset.adminUserSave||(t||n||a).dataset.adminUserToggle||(t||n||a).dataset.adminUserReset),d={user_id:r};if(t&&(d.daily_source_limit=Number(document.querySelector(`[data-user-source="${r}"]`).value||100),d.daily_send_limit=Number(document.querySelector(`[data-user-send="${r}"]`).value||100)),n&&(d.active=n.dataset.active==="true"),a){const p=window.prompt("输入新密码，至少 8 位");if(!p)return;if(p.length<8)throw new Error("密码至少 8 位");d.password=p}await c("/api/admin/user",{method:"POST",body:JSON.stringify(d)}),s("用户已更新"),await q(),await N()}catch(r){s(r.message,"error")}}),h==null||h.addEventListener("click",async e=>{const t=e.target.closest("[data-admin-sender-save]"),n=e.target.closest("[data-admin-sender-toggle]");if(!(!t&&!n))try{const a=Number((t||n).dataset.adminSenderSave||(t||n).dataset.adminSenderToggle),r={sender_id:a};t&&(r.daily_limit=Number(document.querySelector(`[data-sender-limit="${a}"]`).value||100),r.warmup_stage=document.querySelector(`[data-sender-warmup="${a}"]`).value),n&&(r.active=n.dataset.active==="true"),await c("/api/admin/sender",{method:"POST",body:JSON.stringify(r)}),s("发件账号已更新"),await q()}catch(a){s(a.message,"error")}}));document.querySelector("#mark-button").addEventListener("click",async()=>{try{const e=document.querySelector("#mark-id").value,t=document.querySelector("#mark-status").value;await c("/api/mark",{method:"POST",body:JSON.stringify({contact_id:Number(e),status:t})}),s("状态已更新"),await u()}catch(e){s(e.message,"error")}});document.querySelector("#blacklist-button").addEventListener("click",async()=>{try{await c("/api/blacklist",{method:"POST",body:JSON.stringify({email:document.querySelector("#blacklist-email").value||null,domain:document.querySelector("#blacklist-domain").value||null,reason:"dashboard"})}),s("黑名单已更新"),await u()}catch(e){s(e.message,"error")}});document.querySelector("#add-lead-button").addEventListener("click",async()=>{try{const e={linkedin_url:document.querySelector("#lead-linkedin").value,first_name:document.querySelector("#lead-first").value||null,last_name:document.querySelector("#lead-last").value||null,email:document.querySelector("#lead-email").value||null,email_status:document.querySelector("#lead-email").value?"valid":"unknown",status:document.querySelector("#lead-email").value?"enriched":"new",job_title:document.querySelector("#lead-title").value||null,company_name:document.querySelector("#lead-company").value||null,company_domain:document.querySelector("#lead-domain").value||null,source:"manual_dashboard"};if(!e.linkedin_url)throw new Error("LinkedIn URL 必填");const t=await c("/api/contacts",{method:"POST",body:JSON.stringify(e)});s(`新增完成：${JSON.stringify(t)}`),await u()}catch(e){s(e.message,"error")}});document.querySelector("#csv-import-button").addEventListener("click",async()=>{try{const e=document.querySelector("#csv-file").files[0];if(!e)throw new Error("请选择 CSV 文件");const t=await e.text(),n=await c("/api/import/csv",{method:"POST",body:JSON.stringify({csv:t,source:`csv:${e.name}`})});s(`CSV 导入完成：解析 ${n.parsed} 条，新增 ${n.inserted} 条，重复 ${n.skipped} 条`),await u()}catch(e){s(e.message,"error")}});document.querySelector("#source-button").addEventListener("click",async()=>{try{const e={company_website:Pe(document.querySelector("#source-company").value),role:document.querySelector("#source-role").value,industry:document.querySelector("#source-industry").value,location:document.querySelector("#source-location").value,limit:Number(document.querySelector("#source-limit").value||25)};if(!e.role)throw new Error("Role 必填");s("正在调用 Prospeo 自动获客，Limit 越大等待越久...");const t=await c("/api/source",{method:"POST",body:JSON.stringify(e)});T(t.usage);const n=t.result||[0,0];s(`自动获客完成：新增 ${n[0]} 条，重复 ${n[1]} 条`),await u()}catch(e){s(e.message,"error")}});document.querySelector("#linkedin-search-button").addEventListener("click",async()=>{try{const e={role:document.querySelector("#linkedin-role").value.trim(),industry:document.querySelector("#linkedin-industry").value.trim(),location:document.querySelector("#linkedin-location").value.trim(),company_keyword:document.querySelector("#linkedin-company").value.trim(),limit:Number(document.querySelector("#linkedin-limit").value||10),auto_domain_lookup:document.querySelector("#linkedin-auto-domain").checked,auto_generate_email_candidates:document.querySelector("#linkedin-auto-candidates").checked,high_confidence_verify:document.querySelector("#linkedin-high-verify").checked};if(!e.role&&!e.industry&&!e.company_keyword)throw new Error("至少填写职位、行业或公司关键词");s("正在通过 Google 公开索引搜索 LinkedIn 个人主页...");const t=await c("/api/source/linkedin-public-search",{method:"POST",body:JSON.stringify(e)});T(t.usage),o.linkedinTaskId=t.result.task_id,s(`LinkedIn 公网搜索完成：解析 ${t.result.results} 条，入库 ${t.result.promoted} 条，跳过 ${t.result.skipped} 条`),await u(),await v(o.linkedinTaskId)}catch(e){s(e.message,"error")}});document.querySelector("#linkedin-refresh-button").addEventListener("click",async()=>{try{await re(),o.linkedinTaskId&&await v(o.linkedinTaskId),s("LinkedIn 搜索结果已刷新")}catch(e){s(e.message,"error")}});k.addEventListener("click",async e=>{const t=e.target.closest("[data-search-task]"),n=e.target.closest("[data-promote-result]");try{if(t){o.linkedinTaskId=Number(t.dataset.searchTask),await v(o.linkedinTaskId);return}if(n){const a=Number(n.dataset.promoteResult),r=await c("/api/search-results/promote",{method:"POST",body:JSON.stringify({result_id:a})});s(r.contact_id?`已入库联系人 #${r.contact_id}`:"已处理，可能是重复客户"),await u(),o.linkedinTaskId&&await v(o.linkedinTaskId)}}catch(a){s(a.message,"error")}});O.addEventListener("click",async e=>{const t=e.target.closest("[data-life-action]");if(!t)return;const n=Number(t.dataset.id),a=t.dataset.lifeAction;try{if(a==="enrich-email"){s("正在富化当前客户邮箱...");const d=(await c("/api/enrich-one",{method:"POST",body:JSON.stringify({contact_id:n})})).fields||{};d.email_status==="valid"?s(`已找到邮箱：${d.email}`):s("没有找到已验证邮箱，稍后可以换数据源或补充更多客户信息再试","error")}else if(a==="enrich-social"){s("正在富化当前客户社媒...");const r=await c("/api/social-enrich-one",{method:"POST",body:JSON.stringify({contact_id:n})});s(r.ok?"社媒资料已更新":"没有找到可用社媒主页",r.ok?"":"error")}else if(a==="queue-one"){s("正在把当前客户加入发送队列...");const r=await c("/api/queue-one",{method:"POST",body:JSON.stringify({contact_id:n})});s(r.queued?"已加入发送队列":"未能入队：需要先有有效邮箱，且客户不能在黑名单里",r.queued?"":"error")}else if(a==="send-one"){s("正在发送当前客户的下一封邮件...");const r=await c("/api/send-one",{method:"POST",body:JSON.stringify({contact_id:n})});T(r.usage),s(r.sent?"邮件已发送":"未发送：需要先入队、满足发送间隔，并且未超过每日发送上限",r.sent?"":"error")}else if(a==="profile"){s("正在生成客户画像...");const d=(await c("/api/profile-agent",{method:"POST",body:JSON.stringify({contact_id:n})})).insights||{};s(`画像已更新：拟合度 ${d.icp_fit_score??"--"}，下一步：${d.next_action||d.summary||"已生成"}`)}else if(a==="detail")await w(n),s("客户详情已打开");else{const r=Je(a,n);await c("/api/lifecycle",{method:"POST",body:JSON.stringify(r)}),s("生命周期状态已更新")}await u()}catch(r){s(r.message,"error")}});document.querySelector("#save-activity-button").addEventListener("click",async()=>{try{if(!window.selectedContactId)throw new Error("请先选择客户");const e={contact_id:window.selectedContactId,lifecycle_stage:document.querySelector("#activity-stage").value,activity_type:document.querySelector("#activity-type").value,content:document.querySelector("#activity-content").value.trim(),created_by:"dashboard"};if(!e.content)throw new Error("请填写阶段记录");const t=await c("/api/lifecycle-activity",{method:"POST",body:JSON.stringify(e)});return s("阶段记录已保存"),await w(window.selectedContactId),await u(),t}catch(e){s(e.message,"error")}});document.querySelector("#stage-agent-button").addEventListener("click",async()=>{try{if(!window.selectedContactId)throw new Error("请先选择客户");const e={contact_id:window.selectedContactId,lifecycle_stage:document.querySelector("#activity-stage").value,activity_type:document.querySelector("#activity-type").value,content:document.querySelector("#activity-content").value.trim()};s("AI 正在分析当前阶段...");const t=await c("/api/stage-agent",{method:"POST",body:JSON.stringify(e)});Y.innerHTML=oe(t.analysis),s("AI 阶段分析已生成")}catch(e){s(e.message,"error")}});I.addEventListener("click",async e=>{const t=e.target.closest("[data-analyze-activity]");if(t)try{s("AI 正在重新分析记录..."),await c("/api/stage-agent",{method:"POST",body:JSON.stringify({contact_id:window.selectedContactId,activity_id:Number(t.dataset.analyzeActivity)})}),await w(window.selectedContactId),s("记录分析已更新")}catch(n){s(n.message,"error")}});X.addEventListener("click",async e=>{const t=e.target.closest("[data-adopt-email]");if(t)try{const n=Number(t.dataset.contactId),a=t.dataset.adoptEmail;await c("/api/email-candidates/adopt",{method:"POST",body:JSON.stringify({contact_id:n,email:a})}),s(`已采用候选邮箱：${a}`),await w(n),await u()}catch(n){s(n.message,"error")}});document.querySelector("#draft-email-button").addEventListener("click",async()=>{try{if(!window.selectedContactId)throw new Error("请先选择客户");s("正在生成邮件草稿...");const e=await c("/api/email-draft",{method:"POST",body:JSON.stringify({contact_id:window.selectedContactId,mode:document.querySelector("#email-mode").value,subject:document.querySelector("#email-subject").value,body:document.querySelector("#email-body").value})});document.querySelector("#email-subject").value=e.subject||"",document.querySelector("#email-body").value=e.body||"",s("邮件草稿已生成，请检查后再发送")}catch(e){s(e.message,"error")}});document.querySelector("#send-custom-email-button").addEventListener("click",async()=>{try{if(!window.selectedContactId)throw new Error("请先选择客户");const e=document.querySelector("#email-subject").value.trim(),t=document.querySelector("#email-body").value.trim();if(!e||!t)throw new Error("请先填写主题和正文");if(!window.confirm("确认发送给当前客户？dry_run=false 时会真实发出。"))return;s("正在发送邮件...");const n=await c("/api/send-custom",{method:"POST",body:JSON.stringify({contact_id:window.selectedContactId,mode:document.querySelector("#email-mode").value,subject:e,body:t})});s(`邮件已发送：第 ${n.step} 封`),await w(window.selectedContactId),await u()}catch(e){s(e.message,"error")}});async function re(){if(!k||!o.user)return;const t=(await c("/api/search-tasks")).tasks||[];if(!t.length){k.innerHTML=`
      <div class="empty-state compact">
        <strong>还没有 LinkedIn 公网搜索任务</strong>
        <p>填写上方条件后开始搜索，结果会先进入候选池和客户列表，不会自动发邮件。</p>
      </div>
    `;return}o.linkedinTaskId||(o.linkedinTaskId=t[0].id),k.innerHTML=`
    <div class="search-task-strip">
      ${t.slice(0,8).map(n=>`
        <button class="${Number(n.id)===Number(o.linkedinTaskId)?"active":""}" data-search-task="${n.id}">
          <strong>#${n.id} ${i(n.status)}</strong>
          <span>${i(Me(n))}</span>
          <small>结果 ${n.result_count||0} · 入库 ${n.promoted_count||0}</small>
        </button>
      `).join("")}
    </div>
    <div id="linkedin-result-panel" class="search-result-panel"></div>
  `,await v(o.linkedinTaskId)}async function v(e){if(!e)return;const t=document.querySelector("#linkedin-result-panel");if(!t)return;const a=(await c(`/api/search-results?task_id=${encodeURIComponent(e)}`)).results||[];t.innerHTML=Ae(a)}function Ae(e){return e.length?`
    <div class="linkedin-results">
      ${e.map(t=>`
        <article class="linkedin-result ${i(t.status||"")}">
          <header>
            <div>
              <strong>${i(b(t))}</strong>
              <span>${i(t.job_title||"职位待确认")} · ${i(t.company_name||"公司待确认")}</span>
            </div>
            <b>${Number(t.lead_score||0)}</b>
          </header>
          <p>${i(t.raw_snippet||"")}</p>
          <div class="result-meta">
            <span>${i(t.company_domain||"域名待补")}</span>
            <span>${i(t.location||"地区待确认")}</span>
            <span>${i(He(t.status))}</span>
            ${t.failure_reason?`<span class="danger-text">${i(t.failure_reason)}</span>`:""}
          </div>
          <div class="result-candidates">
            ${xe(t.email_candidates||[])}
          </div>
          <footer>
            <a href="${i(t.linkedin_url||t.raw_url||"#")}" target="_blank" rel="noopener">打开 LinkedIn</a>
            ${t.promoted_contact_id?`<span>已入库 #${t.promoted_contact_id}</span>`:`<button data-promote-result="${t.id}">入库</button>`}
          </footer>
        </article>
      `).join("")}
    </div>
  `:'<div class="empty-state compact"><strong>该任务暂无结果</strong><p>如果 Google CSE 没有返回内容，可以放宽职位或地区关键词。</p></div>'}function xe(e){return!Array.isArray(e)||!e.length?'<span class="muted">暂无邮箱候选</span>':e.slice(0,4).map(t=>`
    <span class="candidate-chip ${i(t.category||"")}">
      ${i(t.email||"")}
      <small>${Number(t.confidence||0)}%</small>
    </span>
  `).join("")}function Me(e){const t=e.criteria||{};return[t.role||t.title,t.industry,t.location,t.company_keyword].filter(Boolean).join(" / ")||"公开搜索"}function He(e){return{candidate:"候选",low_score:"低分跳过",promoted:"已入库",duplicate:"重复",failed:"失败"}[e]||e||"未知"}function Je(e,t){const n=Ve(t);if(e==="next")return{contact_id:t,lifecycle_stage:We(n==null?void 0:n.lifecycle_stage),disposition:"active",notes:"dashboard: move to next lifecycle stage"};if(e==="wait"){const a=new Date(Date.now()+6048e5).toISOString();return{contact_id:t,lifecycle_stage:(n==null?void 0:n.lifecycle_stage)||"waiting_pool",disposition:"waiting",next_action_at:a,notes:"dashboard: move to waiting follow-up pool"}}return{contact_id:t,lifecycle_stage:"abandoned",disposition:"abandoned",lost_reason:"dashboard: manually abandoned",notes:"dashboard: abandon customer lifecycle"}}async function w(e){const t=await c(`/api/contact-detail?contact_id=${encodeURIComponent(e)}`);if(!t.contact)throw new Error("客户不存在");window.selectedContactId=e,window.selectedContactDetail=t,me.classList.add("hidden"),X.classList.remove("hidden"),Re(t.contact),Be(t.activities||[]),Y.innerHTML="",document.querySelector("#activity-stage").value=t.contact.lifecycle_stage||"lead",document.querySelector("#email-mode").value="ai",document.querySelector("#email-subject").value=`Quick question about ${t.contact.company_name||"your business"}`,document.querySelector("#email-body").value="",document.querySelector("#activity-content").value="",document.querySelector("#customer-workspace").scrollIntoView({behavior:"smooth",block:"start"})}function Re(e){const t=e.profile_insights||{};pe.innerHTML=`
    <div>
      <strong>${i(b(e))}</strong>
      <span>${i(e.job_title||"")} · ${i(e.company_name||"")}</span>
    </div>
    <div>
      <b>${i(J(e.lifecycle_stage))}</b>
      <span>${i(ae(e.disposition))}</span>
    </div>
    <div>
      <b>${t.icp_fit_score??"--"}</b>
      <span>拟合度 / ${i(ie(t.intent_level))}</span>
    </div>
    <p>${i(e.profile_summary||"还没有客户画像，点击列表里的“画像”生成。")}</p>
    ${Ue(e)}
  `}function Ue(e){const t=Array.isArray(e.email_candidates)?e.email_candidates:[];return t.length?`
    <section class="email-candidates">
      <header><strong>邮箱候选</strong><span>只把个人 valid 邮箱作为正式发信邮箱</span></header>
      ${t.slice(0,6).map(a=>`
    <div class="candidate-row ${i(a.category||"")}">
      <strong>${i(a.email||"")}</strong>
      <span>${i(ne(a.source||""))}</span>
      <span>${i(De(a.category))}</span>
      <span>${i(a.status||"unknown")}</span>
      <b>${Number(a.confidence||0)}%</b>
      ${a.category==="personal_work"?`<button data-adopt-email="${i(a.email||"")}" data-contact-id="${e.id}">采用</button>`:""}
    </div>
  `).join("")}
    </section>
  `:`
      <section class="email-candidates empty">
        <header><strong>邮箱候选</strong><span>暂无候选</span></header>
      </section>
    `}function De(e){return{personal_work:"个人工作邮箱",personal_free:"个人邮箱",company_generic:"公司通用邮箱"}[e]||"未分类"}function Be(e){if(!e.length){I.innerHTML='<div class="empty-activity">还没有阶段记录。</div>';return}I.innerHTML=e.map(t=>`
    <article class="activity-card">
      <header>
        <strong>${i(J(t.lifecycle_stage))} / ${i(Fe(t.activity_type))}</strong>
        <span>${E(t.created_at)}</span>
      </header>
      <p>${i(t.content)}</p>
      ${oe(t.ai_analysis)}
      <button data-analyze-activity="${t.id}">重新分析</button>
    </article>
  `).join("")}function Fe(e){return{reply:"回复内容",research:"客户资料/背景调研",meeting_note:"会议纪要",business_plan:"商业计划",trial_order:"试订单",agreement_review:"代理协议风险",store_plan:"门店创建资料",note:"普通备注"}[e]||e}function oe(e){if(!e||!Object.keys(e).length)return"";const t=(n,a)=>a!=null&&a.length?`<div><b>${n}</b>${a.map(r=>`<span>${i(r)}</span>`).join("")}</div>`:"";return`
    <section class="analysis-card">
      <strong>${i(e.summary||"AI 阶段分析")}</strong>
      ${t("下一步",e.next_steps)}
      ${t("缺失资料",e.missing_info)}
      ${t("风险",e.risks)}
      ${t("准备材料",e.materials_to_prepare)}
    </section>
  `}function Ve(e){var t;return(t=window.latestContacts)==null?void 0:t.find(n=>Number(n.id)===Number(e))}function We(e){const t=["lead","replied","conversation","meeting","business_plan","store_visit","trial_order","agency_agreement","hq_visit","signed","maintenance"],n=t.indexOf(e||"lead");return t[Math.min(n+1,t.length-1)]}ge();
//# sourceMappingURL=legacy-controller-Cb8eg-p5.js.map
