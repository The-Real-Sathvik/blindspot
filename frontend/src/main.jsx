import React, {useCallback, useEffect, useRef, useState} from 'react';
import {createRoot} from 'react-dom/client';
import {ArrowRight, BookOpen, ChevronDown, FileText, Menu, Plus, Search, ShieldCheck, X} from 'lucide-react';
import {displayAuthors, documentLabel, request} from './api';
import {ProfileMenu} from './profile-menu.js';
import {createWorkspace, jobPaths, preferredPaperId, resolveWorkspace, visibleBundle} from './workflow.js';
import {alternativePreparationSummary, eligibleAlternatives} from './availability.js';
import {milestones, threadState} from './thread-state.js';
import {EvidencePanel} from './thread-panel.jsx';
import {preparationSummary} from './preparation-state.js';
import {PreparationRow} from './preparation-view.js';
import './styles.css';
import './workflow.css';
import './claim-controls.css';
import './thread.css';

const WORKSPACE_KEY='blindspot.activeResearchJob';

function App() {
  const [jobs,setJobs]=useState([]), [selected,setSelected]=useState(null), [bundle,setBundle]=useState(null);
  const [topic,setTopic]=useState(''), [cloudResearchConsent,setCloudResearchConsent]=useState(false), [paperId,setPaperId]=useState(null), [busy,setBusy]=useState('');
  const [error,setError]=useState(''), [notice,setNotice]=useState(''), [failures,setFailures]=useState({});
  const [preparation,setPreparation]=useState(null), [needsRefresh,setNeedsRefresh]=useState(false);
  const [expanded,setExpanded]=useState(null), [panel,setPanel]=useState(null), [sidebarOpen,setSidebarOpen]=useState(false), [localPanel,setLocalPanel]=useState(null), [gapConsent,setGapConsent]=useState(false);
  const activeId=useRef(null), sequence=useRef(0);
  const sidebarRef=useRef(null), menuRef=useRef(null);
  const closePanel=useCallback(()=>setPanel(null),[]);
  const loadJobs=useCallback(async()=>{const data=await request('/jobs?limit=100');setJobs(data);return data;},[]);
  const loadJob=useCallback(async(id,{resetPaper=false}={})=>{
    const call=++sequence.current;
    const [details,papers,docs,claims,gaps]=await Promise.all(jobPaths(id).map(path=>request(path)));
    if(activeId.current!==id || call!==sequence.current) return;
    setBundle({jobId:id,details,papers,docs,claims,gaps});
    setPaperId(previous=>!resetPaper && papers.some(p=>p.id===previous) ? previous : (papers.find(p=>p.included===true)?.id || preferredPaperId(papers) || papers[0]?.id || null));
  },[]);
  function chooseJob(id) {
    activeId.current=id;sequence.current+=1;setBundle(null);setSelected(id);setPaperId(null);setExpanded(null);setPanel(null);setSidebarOpen(false);setError('');setNotice('');setFailures({});setPreparation(null);setNeedsRefresh(false);
    try {if(id)localStorage.setItem(WORKSPACE_KEY,id);else localStorage.removeItem(WORKSPACE_KEY);} catch {/* Selection preference is optional. */}
    if(id && id===selected) loadJob(id).catch(err=>{if(activeId.current===id)setError(err.message);});
    if(!id) requestAnimationFrame(()=>document.getElementById('research-topic')?.focus());
  }
  useEffect(()=>{let cancelled=false;loadJobs().then(data=>{if(cancelled)return;let saved=null;try{saved=localStorage.getItem(WORKSPACE_KEY);}catch{/* Optional preference. */}const initial=resolveWorkspace(data,saved);if(initial){activeId.current=initial;setSelected(initial);}}).catch(err=>{if(!cancelled)setError(err.message);});return()=>{cancelled=true;};},[loadJobs]);
  useEffect(()=>{if(selected)loadJob(selected).catch(err=>{if(activeId.current===selected)setError(err.message);});},[selected,loadJob]);
  useEffect(()=>{
    if(!sidebarOpen)return undefined;
    sidebarRef.current?.querySelector('button:not(:disabled)')?.focus();
    function key(event){
      if(event.key==='Escape'){event.preventDefault();setSidebarOpen(false);menuRef.current?.focus();return;}
      if(event.key!=='Tab')return;
      const items=[...(sidebarRef.current?.querySelectorAll('button:not(:disabled)')||[])];
      if(!items.length)return;
      if(event.shiftKey&&document.activeElement===items[0]){event.preventDefault();items[items.length-1].focus();}
      if(!event.shiftKey&&document.activeElement===items[items.length-1]){event.preventDefault();items[0].focus();}
    }
    document.addEventListener('keydown',key);return()=>document.removeEventListener('keydown',key);
  },[sidebarOpen]);
  async function createJob(event){event.preventDefault();if(!topic.trim()||!cloudResearchConsent||busy)return;setBusy('create');setError('');setNotice('');try{const job=await createWorkspace(request,topic);activeId.current=job.id;setSelected(job.id);setTopic('');await request(`/jobs/${encodeURIComponent(job.id)}/run`,{method:'POST',body:JSON.stringify({cloud_consent:true}),signal:AbortSignal.timeout(60000)});await Promise.all([loadJobs(),loadJob(job.id,{resetPaper:true})]);setNotice('Automatic research finished. Review the evidence-linked report and recorded limitations.');}catch(err){setError(err.message);}finally{setCloudResearchConsent(false);setBusy('');}}
  async function discover(){if(!selected||busy)return;const id=selected;setBusy('discover');setError('');setNotice('');setFailures(p=>({...p,papers:false}));try{const result=await request(`/jobs/${encodeURIComponent(id)}/discover`,{method:'POST'});await Promise.all([loadJobs(),loadJob(id,{resetPaper:true})]);if(activeId.current===id){setExpanded(null);setPreparation(null);setNotice(`Discovery returned ${result.retrieved} records and saved ${result.shortlisted} shortlisted papers.`);}}catch(err){if(activeId.current===id){setError(err.message);setFailures(p=>({...p,papers:true}));}}finally{setBusy('');}}
  async function processPaper(action){if(!selected||!paperId||busy)return;const id=selected,target=paperId;setBusy(action);setError('');setNotice('');setFailures(p=>({...p,evidence:false}));try{const result=await request(`/jobs/${encodeURIComponent(id)}/papers/${encodeURIComponent(target)}/${action}`,{method:'POST',signal:AbortSignal.timeout(60000)});await loadJob(id);if(activeId.current===id){if(['failed','unsupported','unavailable'].includes(result.status))setError(result.message);else setNotice(result.message);setExpanded(null);}}catch(err){if(activeId.current===id){setError(err.message);setFailures(p=>({...p,evidence:true}));if(err.code==='local_api_timeout')setNeedsRefresh(true);}}finally{setBusy('');}}
  async function uploadPaper(file){if(!selected||!paperId||busy)return;const id=selected,target=paperId;setBusy('upload');setError('');setNotice('');try{const result=await request(`/jobs/${encodeURIComponent(id)}/papers/${encodeURIComponent(target)}/upload`,{method:'POST',headers:{'Content-Type':'application/pdf'},body:file,signal:AbortSignal.timeout(60000)});await loadJob(id);if(activeId.current===id)setNotice(result.reused?'Existing PDF reused.':'PDF saved. Review its association before cloud analysis.');}catch(err){if(activeId.current===id)setError(err.message);}finally{setBusy('');}}
  async function prepareShortlist(){if(!selected||busy||needsRefresh)return;const id=selected;setBusy('prepare');setError('');setNotice('');setPreparation(null);setFailures(p=>({...p,evidence:false}));try{const result=await request(`/jobs/${encodeURIComponent(id)}/prepare-shortlist`,{method:'POST',signal:AbortSignal.timeout(60000)});await loadJob(id,{resetPaper:true});if(activeId.current===id){setPreparation(result);setExpanded('fulltext');setNotice('Preparation finished. Review the results below.');}}catch(err){if(activeId.current===id){setError(err.message);setFailures(p=>({...p,evidence:true}));if(err.message.includes('timed out'))setNeedsRefresh(true);}}finally{setBusy('');}}
  async function refreshPreparation(){if(!selected||busy)return;setBusy('refresh');setError('');try{await loadJob(selected);setNeedsRefresh(false);setFailures(p=>({...p,evidence:false}));setNotice('Status refreshed. The server prevents overlapping processing.');}catch(err){setError(err.message);}finally{setBusy('');}}
  async function detectGaps(){if(!selected||busy||!gapConsent)return;const id=selected;setBusy('gaps');setError('');setNotice('');try{const result=await request(`/jobs/${encodeURIComponent(id)}/gaps/detect`,{method:'POST',body:JSON.stringify({cloud_consent:true})});await loadJob(id);if(activeId.current===id)setNotice(result.status==='candidates'?`Saved ${result.accepted_count} provisional gap candidate${result.accepted_count===1?'':'s'}.`:'Gap detection abstained; no candidate was saved.');}catch(err){if(activeId.current===id)setError(err.message);}finally{setGapConsent(false);setBusy('');}}
  const current=visibleBundle(bundle,selected);
  const papers=current?.papers||[], docs=current?.docs||[], claims=current?.claims||[], gaps=current?.gaps||[], details=current?.details;
  const flow=threadState(current,{busy,failures,needsRefresh});
  const showStage=expanded ?? flow.active;
  const shortlist=papers.filter(p=>p.included===true), other=papers.filter(p=>p.included!==true), alternatives=eligibleAlternatives(papers);
  const preparationCounts=preparationSummary(shortlist);
  const alternativePreparation=alternativePreparationSummary(papers);
  const totalEvidenceReady=preparationCounts.ready+alternativePreparation.evidenceReady;
  const allChunks=docs.flatMap(d=>(d.chunks||[]).map(c=>({document:d,chunk:c})));
  function openPaper(id){setPaperId(id);setPanel({kind:'paper',paperId:id});}
  function openChunk(d,c){setPaperId(d.paper_id);setPanel({kind:'chunk',paperId:d.paper_id,documentId:d.id,chunkId:c.id});}
  function showMilestone(id){setExpanded(id);requestAnimationFrame(()=>document.getElementById(`stage-${id}`)?.scrollIntoView({block:'nearest',behavior:'smooth'}));}
  function toggleMilestone(id){setExpanded(current => (current ?? flow.active) === id ? '' : id);}
  function nextAction(){switch(flow.action.kind){case'discover':discover();break;case'new':chooseJob(null);break;case'prepare':prepareShortlist();break;case'refresh':refreshPreparation();break;case'detect_gaps':showMilestone('gaps');break;case'review_papers':showMilestone('papers');if(paperId)openPaper(paperId);break;case'review_options':showMilestone('fulltext');if(paperId)openPaper(paperId);break;case'review_failure':showMilestone('fulltext');if(flow.action.paperId)openPaper(flow.action.paperId);break;case'review_alternative':showMilestone('fulltext');if(flow.alternatives?.[0])openPaper(flow.alternatives[0].id);break;case'inspect_evidence':{const item=flow.usable?.[0]||flow.chunks?.[0];if(item)openChunk(item.document,item.chunk);break;}case'review_claims':if(claims[0])setPanel({kind:'claim',claimId:claims[0].id});break;default:document.getElementById('research-topic')?.focus();}}
  return <div className="app-shell thread-shell">
    {sidebarOpen && <button className="sidebar-backdrop" aria-label="Close workspaces" onClick={()=>setSidebarOpen(false)}/>}
    <aside ref={sidebarRef} className={`sidebar workflow-sidebar thread-sidebar ${sidebarOpen?'mobile-open':''}`} aria-label="Workspaces">
      <div className="brand"><div className="brand-mark"><span/><span/></div><div><strong>blindspot<span>.</span></strong><small>RESEARCH WORKSPACE</small></div></div>
      <button className="new-research" disabled={!!busy} onClick={()=>chooseJob(null)}><Plus size={16}/> New research</button>
      <div className="side-label workspace-label">WORKSPACES <span>{jobs.length}</span></div>
      <nav className="workspace-list" aria-label="Saved research jobs">{jobs.length?jobs.map(job=><button key={job.id} data-workspace-id={job.id} disabled={!!busy} className={`workspace-item ${selected===job.id?'active':''}`} aria-current={selected===job.id?'page':undefined} onClick={()=>chooseJob(job.id)}><BookOpen size={15}/><span>{job.topic}<small>{formatDate(job.created_at)}</small></span></button>):<p className="side-empty">No workspaces yet. Start with a research idea.</p>}</nav>
      <div className="side-bottom"><ShieldCheck size={16}/><div><strong>Evidence first</strong><span>Built for careful research</span></div></div>
    </aside>
    <main className="main thread-main"><header className="topbar thread-topbar"><div className="thread-heading"><button ref={menuRef} className="icon-button mobile-menu" aria-label="Open workspaces" aria-expanded={sidebarOpen} onClick={()=>setSidebarOpen(true)}><Menu size={20}/></button><div><span>WORKSPACE</span><strong title={details?.topic||'New research'}>{details?.topic||(selected?'Loading workspace…':'New research')}</strong></div></div><div className="top-right"><span className="local-status">Local workspace</span><ProfileMenu onChoose={setLocalPanel}/></div></header>
      <div className={`thread-layout ${panel?'has-panel':''}`}><div className="thread-scroll"><div className="thread-content">
        {error && <div className="alert error" role="alert"><span>{error}</span><button onClick={()=>setError('')} aria-label="Dismiss error"><X size={16}/></button></div>}
        {notice && <div className="alert" role="status"><span>{notice}</span><button onClick={()=>setNotice('')} aria-label="Dismiss notification"><X size={16}/></button></div>}
        {localPanel && <div className="local-info"><button className="local-info-close" onClick={()=>setLocalPanel(null)} aria-label="Close"><X size={16}/></button><h2>{localPanel==='profile'?'Local workspace':'Current setup'}</h2><p>{localPanel==='profile'?'This single-user application has no signed-in account.':'Research records are stored locally. Cloud extraction requires explicit consent each time.'}</p></div>}
        {!selected ? <div className="thread-welcome"><div className="eyebrow"><span className="eyebrow-dot"/> YOUR RESEARCH, IN FOCUS</div><h1>Start with a question.</h1><p>Find papers, examine their sources, and follow the evidence.</p><form className="research-box" onSubmit={createJob}><label htmlFor="research-topic">What would you like to explore?</label><textarea id="research-topic" value={topic} maxLength={4000} onChange={e=>setTopic(e.target.value)} placeholder="Describe your research idea…" rows="4"/><label className="cloud-consent"><input type="checkbox" checked={cloudResearchConsent} onChange={e=>setCloudResearchConsent(e.target.checked)}/> I consent to source evidence being sent to Gemini for bounded automatic claim extraction.</label><div className="compose-footer"><span>Creates a local research workspace.</span><button className="primary" disabled={!topic.trim()||!cloudResearchConsent||!!busy}><Plus size={15}/> Create workspace</button></div></form></div> : !current ? <div role="status" className="workspace-loading">{error?'The workspace could not be loaded. Select it again to retry.':'Loading research workspace…'}</div> : <>
          <div className="thread-intro"><span className="mini-label">RESEARCH THREAD</span><h1>{details.topic}</h1><p>A clear path from research idea to source-linked findings.</p></div>
          <nav className="journey-tracker" aria-label="Research progress">{milestones.map((m,i)=><button key={m.id} onClick={()=>showMilestone(m.id)} aria-current={showStage===m.id?'step':undefined}><span className={`tracker-dot ${flow.states[m.id]}`}>{['completed','completed_with_alternatives'].includes(flow.states[m.id])?'✓':i+1}</span><span>{m.label}<small>{labelState(flow.states[m.id])}</small></span></button>)}</nav>
          <div className="thread-cards">
            <Milestone id="topic" number="01" title="Research topic" summary={details.topic} state={flow.states.topic} open={showStage==='topic'} onToggle={toggleMilestone}><p>{details.topic}</p><p className="muted">Created {formatDate(details.created_at)} · {papers.length} saved papers</p>{details.warnings?.map(w=><p className="warning" key={w}>{w}</p>)}</Milestone>
            <Milestone id="papers" number="02" title="Paper discovery" summary={papers.length?`${papers.length} saved · ${shortlist.length} shortlisted`:'Find relevant papers for this topic'} state={flow.states.papers} open={showStage==='papers'} onToggle={toggleMilestone}>
              <div className="milestone-toolbar"><p>Saved shortlist <strong>{shortlist.length}</strong></p><span className="muted">Discovery runs automatically when a workspace is created.</span></div>
              {shortlist.length?shortlist.map(p=><PaperRow key={p.id} paper={p} onClick={()=>openPaper(p.id)}/>):<p className="thread-empty">{papers.length?'No shortlisted papers were saved.':'Search when you are ready. Your topic is saved.'}</p>}
              {other.length>0 && <details className="other-list"><summary>Other discovered papers ({other.length})</summary>{other.map(p=><PaperRow key={p.id} paper={p} onClick={()=>openPaper(p.id)}/>)}</details>}
              {details.searches?.length>0 && <details className="advanced"><summary>Search activity</summary>{details.searches.map((s,i)=><p key={i}>{s.source}: {s.failed?'Search failed':`${s.retrieved} records retrieved`}</p>)}</details>}
            </Milestone>
            <Milestone id="fulltext" number="03" title="Full-text preparation" summary={shortlist.length?`${shortlist.filter(p=>p.document?.status==='extracted').length} of ${shortlist.length} shortlisted papers have evidence${alternativePreparation.evidenceReady?` · ${alternativePreparation.evidenceReady} alternative source ready`:''}`:'Available after discovery'} state={flow.states.fulltext} open={showStage==='fulltext'} onToggle={toggleMilestone}>
              {!papers.length?<p className="thread-empty">Automatic discovery has not yet saved papers for this job.</p>:<><p><strong>{totalEvidenceReady}</strong> evidence-ready paper{totalEvidenceReady===1?'':'s'} available for this research job.</p><div className="preparation-counts" aria-label="Shortlist preparation summary"><span><strong>{preparationCounts.ready}</strong> shortlisted evidence ready</span><span><strong>{preparationCounts.processing}</strong> shortlisted awaiting processing</span><span><strong>{preparationCounts.needs_pdf}</strong> shortlisted papers lack supported PDFs</span>{preparationCounts.failed>0 && <span><strong>{preparationCounts.failed}</strong> shortlisted acquisition failed</span>}{preparationCounts.unknown>0 && <span><strong>{preparationCounts.unknown}</strong> shortlist status unavailable</span>}</div>{shortlist.map(p=><PreparationRow key={p.id} paper={p} onInspect={()=>openPaper(p.id)}/>)}<p className="muted">The original shortlist is preserved. Its missing-PDF count does not prevent evidence recovery from job-associated alternatives.</p>{alternativePreparation.evidenceReady>0 && <p className="muted">Automatic recovery prepared evidence from {alternativePreparation.evidenceReady} job-associated alternative source{alternativePreparation.evidenceReady===1?'':'s'}. This does not establish scientific verification or research gaps.</p>}{(alternativePreparation.failed>0||preparationCounts.failed>0) && <details className="advanced"><summary>Technical details: {alternativePreparation.failed+preparationCounts.failed} recorded acquisition failure{alternativePreparation.failed+preparationCounts.failed===1?'':'s'}</summary><p>Failures are preserved per paper and are not retried automatically. Inspect a paper for its sanitized source state.</p></details>}{alternatives.length>0 && <details className="other-list"><summary>Eligible alternatives ({alternatives.length})</summary>{alternatives.map(p=><PreparationRow key={p.id} paper={p} onInspect={()=>openPaper(p.id)}/>)}</details>}</>}
            </Milestone>
            <Milestone id="evidence" number="04" title="Evidence" summary={allChunks.length?`${allChunks.length} saved passages available`:'Unlocks when PDF text is prepared'} state={flow.states.evidence} open={showStage==='evidence'} onToggle={toggleMilestone}>
              {allChunks.length?docs.filter(d=>d.chunks?.length).map(d=><div className="evidence-paper" key={d.id}><strong>{d.paper_title}</strong><span>{d.chunks.length} source-verified passages prepared automatically</span><button className="secondary" onClick={()=>openChunk(d,d.chunks[0])}>View supporting evidence</button></div>):<p className="thread-empty">Automatic source preparation has not produced page-linked evidence yet.</p>}
            </Milestone>
            <Milestone id="findings" number="05" title="Findings" summary={claims.length?`${claims.length} saved ${claims.length===1?'claim':'claims'}`:'Unlocks after explicit passage analysis'} state={flow.states.findings} open={showStage==='findings'} onToggle={toggleMilestone}>
              {claims.length?claims.map(c=><button key={c.id} className="finding-row" onClick={()=>setPanel({kind:'claim',claimId:c.id})}><strong>{c.statement}</strong><span>{c.paper_title} · Source link and scientific support are separate checks.</span><span>Scientific support: {c.semantic_status==='unverified'?'Not independently checked':c.semantic_status}</span></button>):<p className="thread-empty">{flow.states.findings==='abstained'?'The latest run returned no claims.':allChunks.length?'Open a source passage to review the optional cloud consent.':'Prepare evidence before analyzing a passage.'}</p>}
              {details.runs?.filter(r=>r.role==='claim_extraction' && (r.status==='failed'||r.outcome==='partial_error')).slice(0,1).map((r,i)=><p className="warning" key={i}>Latest extraction issue: {r.error_category||'inference failed'}. Saved work is unchanged.</p>)}
            </Milestone>
            <Milestone id="gaps" number="06" title="Research gaps" summary={gaps.length?`${gaps.length} provisional candidates saved`:'Requires independently supported findings from multiple papers'} state={flow.states.gaps} open={showStage==='gaps'} onToggle={toggleMilestone}>
              {gaps.length?gaps.map(g=><button className="finding-row" key={g.id} onClick={()=>setPanel({kind:'gap',gap:g})}><strong>{g.statement}</strong><span>Provisional · {g.category.replaceAll('_',' ')}</span></button>):<p className="thread-empty">Gap detection is unavailable. Comparing papers requires independently supported claims from at least two papers; source links alone do not establish support.</p>}
              {!gaps.length && flow.supportedPapers?.size>=2 && <div className="panel-note"><p>Human-supported claims exist, but automatic gap generation is intentionally unavailable. It requires a separately approved provider run and remains provisional.</p></div>}
              <p>Saved candidates are provisional and not established as globally novel.</p>
              <a className="secondary thread-action" href={`/api/jobs/${encodeURIComponent(selected)}/report.pdf`}>Export PDF report</a>
            </Milestone>
          </div>
        </>}
      </div>{current && <div className="next-action"><div><span className="mini-label">REPORT</span><p>{details.error_info || 'This workspace runs its bounded collection stages automatically. Review the saved report and its limitations.'}</p></div><a className="primary" href={`/api/jobs/${encodeURIComponent(selected)}/report.pdf`}>View PDF<ArrowRight size={15}/></a></div>}</div>
      <EvidencePanel panel={panel} close={closePanel} jobId={selected} papers={papers} docs={docs} claims={claims} readOnly busy={busy} setBusy={setBusy} processPaper={processPaper} uploadPaper={uploadPaper} refresh={()=>loadJob(selected)} openPanel={setPanel} openPaper={openPaper} onReviewClaims={()=>{closePanel();showMilestone('findings');}}/>
      </div>
    </main>
  </div>;
}

function Milestone({id,number,title,summary,state,open,onToggle,children}) {return <section id={`stage-${id}`} className={`milestone ${open?'open':''}`}><button className="milestone-header" aria-expanded={open} onClick={()=>onToggle(id)}><span className="milestone-number">{number}</span><span className="milestone-title"><strong>{title}</strong><small>{summary}</small></span><span className={`milestone-state ${state}`}>{labelState(state)}</span><ChevronDown size={16}/></button>{open && <div className="milestone-body">{children}</div>}</section>;}
function PaperRow({paper,onClick}) {return <button className="paper-row" onClick={onClick}><span><strong>{paper.title}</strong><small>{displayAuthors(paper.authors)} · {paper.year||'Year not provided'} · {paper.source}</small><small>Full text: {documentLabel(paper.document?.status)}</small></span><ArrowRight size={15}/></button>;}
function labelState(state){return ({completed:'Complete',completed_with_alternatives:'Completed with alternatives',partial:'Partly ready',in_progress:'In progress',not_started:'Not started',blocked:'Not ready',failed:'Needs attention',abstained:'No claims found',insufficient_evidence:'Not ready'})[state]||'Not started';}
function formatDate(value){if(!value)return 'Date unavailable';const date=new Date(value);return Number.isNaN(date.getTime())?'Date unavailable':date.toLocaleDateString(undefined,{month:'short',day:'numeric',year:'numeric'});}

createRoot(document.getElementById('root')).render(<App/>);
