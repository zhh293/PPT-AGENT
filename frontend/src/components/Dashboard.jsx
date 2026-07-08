import { useState, useEffect, useRef } from 'react'
import PhaseProgress from './PhaseProgress'
import EventStream from './EventStream'
import ArtifactViewer from './ArtifactViewer'

export default function Dashboard({ jobId }) {
  const [job, setJob] = useState(null)
  const [artifacts, setArtifacts] = useState([])
  const [events, setEvents] = useState([])
  const [activeTab, setActiveTab] = useState('progress')
  const [running, setRunning] = useState(false)
  const [genProgress, setGenProgress] = useState(null)
  const eventSourceRef = useRef(null)

  // Fetch job info
  const fetchJob = async () => {
    const res = await fetch(`/api/jobs/${jobId}`)
    const data = await res.json()
    setJob(data)
  }

  // Fetch artifacts
  const fetchArtifacts = async () => {
    const res = await fetch(`/api/jobs/${jobId}/artifacts`)
    const data = await res.json()
    setArtifacts(data)
  }

  // Fetch events history
  const fetchEvents = async () => {
    const res = await fetch(`/api/jobs/${jobId}/events?limit=200`)
    const data = await res.json()
    setEvents(data)
  }

  // SSE connection
  useEffect(() => {
    const es = new EventSource(`/api/jobs/${jobId}/stream`)
    eventSourceRef.current = es

    es.onmessage = (e) => {
      try {
        const event = JSON.parse(e.data)
        if (event.type === 'connected') return

        setEvents((prev) => [...prev.slice(-500), event])

        // Refresh job status and artifacts on phase events
        if (event.type === 'phase_completed' || event.type === 'artifact_written') {
          fetchJob()
          fetchArtifacts()
        }
      } catch (err) {
        console.error('SSE parse error:', err)
      }
    }

    es.onerror = () => {
      console.warn('SSE connection error, will retry...')
    }

    return () => es.close()
  }, [jobId])

  // Poll for image generation progress during visual_generation phase
  useEffect(() => {
    if (job?.current_phase !== 'visual_generation' && job?.status !== 'running') return
    const fetchGenProgress = async () => {
      try {
        const res = await fetch(`/api/jobs/${jobId}/generation-progress`)
        const data = await res.json()
        setGenProgress(data)
      } catch {}
    }
    fetchGenProgress()
    const interval = setInterval(fetchGenProgress, 3000)
    return () => clearInterval(interval)
  }, [jobId, job?.current_phase, job?.status])

  useEffect(() => {
    fetchJob()
    fetchArtifacts()
    fetchEvents()
  }, [jobId])

  // Start job run
  const handleRun = async () => {
    setRunning(true)
    try {
      await fetch(`/api/jobs/${jobId}/run`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ force: false }),
      })
      // Job started, SSE will push updates
      setTimeout(fetchJob, 1000)
    } catch (e) {
      console.error('Failed to start job:', e)
    } finally {
      setRunning(false)
    }
  }

  // Approve
  const [approving, setApproving] = useState(false)
  const handleApprove = async () => {
    setApproving(true)
    try {
      const res = await fetch(`/api/jobs/${jobId}/approve`, { method: 'POST' })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      fetchArtifacts()
      fetchJob()
    } catch (e) {
      console.error('Approve failed:', e)
      alert('审核失败: ' + e.message)
    } finally {
      setApproving(false)
    }
  }

  if (!job) return <div className="text-gray-500">加载中...</div>

  return (
    <div className="space-y-6">
      {/* Job Header */}
      <div className="bg-gray-900 border border-gray-800 rounded-xl p-5">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-xl font-semibold text-white">{job.job_id}</h2>
            <p className="text-sm text-gray-500 mt-1">
              状态: <span className="text-gray-300">{job.status}</span>
              {job.current_phase && <> · 当前: <span className="text-blue-400">{job.current_phase}</span></>}
            </p>
          </div>
          <div className="flex gap-2">
            {job.status !== 'completed' && (
              <button
                onClick={handleRun}
                disabled={running}
                className="px-4 py-2 bg-green-600 hover:bg-green-500 disabled:bg-gray-700 text-white text-sm font-medium rounded-lg transition-colors"
              >
                {running ? '启动中...' : '▶ 运行'}
              </button>
            )}
            {artifacts.some(a => a.name === 'slide_contents') && job.status !== 'completed' && (
              <button
                onClick={handleApprove}
                disabled={approving}
                className="px-4 py-2 bg-amber-600 hover:bg-amber-500 disabled:bg-gray-700 text-white text-sm font-medium rounded-lg transition-colors"
              >
                {approving ? '审核中...' : '✓ 审核通过'}
              </button>
            )}
            {artifacts.some(a => a.name === 'final_pptx') && (
              <a
                href={`/api/jobs/${jobId}/artifacts/final_pptx`}
                className="px-4 py-2 bg-purple-600 hover:bg-purple-500 text-white text-sm font-medium rounded-lg transition-colors inline-block"
              >
                ↓ 下载 PPT
              </a>
            )}
          </div>
        </div>
      </div>

      {/* Image Generation Progress Bar */}
      {genProgress && genProgress.total > 0 && (
        <div className="bg-gray-900 border border-gray-800 rounded-xl p-4">
          <div className="flex items-center justify-between mb-2">
            <span className="text-sm font-semibold text-white">图片生成进度</span>
            <span className="text-xs text-gray-500">{genProgress.progress} (成功{genProgress.success}/失败{genProgress.failed})</span>
          </div>
          <div className="w-full bg-gray-800 rounded-full h-2.5 overflow-hidden">
            <div className="h-full rounded-full transition-all duration-500 bg-gradient-to-r from-blue-500 to-green-500" style={{width:`${genProgress.total>0?(genProgress.done/genProgress.total)*100:0}%`}}/>
          </div>
          {genProgress.failed > 0 && (
            <details className="mt-2">
              <summary className="text-xs text-red-400 cursor-pointer">失败详情 ({genProgress.failed})</summary>
              <div className="mt-1 max-h-28 overflow-y-auto text-xs text-gray-400 space-y-0.5">
                {(genProgress.details?.failed||[]).map((f,i)=><div key={i}><span className="text-red-400">slide {f.index}:</span> {f.reason}</div>)}
              </div>
            </details>
          )}
        </div>
      )}

      {/* Phase Progress */}
      <PhaseProgress phases={job.phases_completed} currentPhase={job.current_phase} />

      {/* Tabs */}
      <div className="flex gap-1 border-b border-gray-800">
        {[
          { id: 'progress', label: '产物' },
          { id: 'events', label: '事件流' },
        ].map((tab) => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            className={`px-4 py-2 text-sm font-medium transition-colors border-b-2 -mb-px ${
              activeTab === tab.id
                ? 'border-blue-500 text-white'
                : 'border-transparent text-gray-500 hover:text-gray-300'
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Tab Content */}
      {activeTab === 'progress' && <ArtifactViewer jobId={jobId} artifacts={artifacts} />}
      {activeTab === 'events' && <EventStream events={events} />}
    </div>
  )
}
