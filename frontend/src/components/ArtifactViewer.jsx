import { useState } from 'react'

const PHASE_LABELS = {
  document_analysis: '文档分析',
  outline_generation: '大纲生成',
  template_matching: '模板匹配',
  design_planning: '设计规划',
  content_mapping: '内容映射',
  visual_generation: '图片生成',
  ppt_assembly: 'PPT 组装',
  verification: '验证',
}

function formatSize(bytes) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

export default function ArtifactViewer({ jobId, artifacts }) {
  const [expanded, setExpanded] = useState(null)
  const [content, setContent] = useState(null)
  const [loading, setLoading] = useState(false)

  const viewArtifact = async (name) => {
    if (expanded === name) {
      setExpanded(null)
      setContent(null)
      return
    }

    if (name === 'final_pptx') {
      // Can't preview PPTX inline, just download
      window.open(`/api/jobs/${jobId}/artifacts/final_pptx`, '_blank')
      return
    }

    setLoading(true)
    setExpanded(name)
    try {
      const res = await fetch(`/api/jobs/${jobId}/artifacts/${name}`)
      const data = await res.json()
      setContent(data)
    } catch (e) {
      setContent({ error: e.message })
    } finally {
      setLoading(false)
    }
  }

  if (artifacts.length === 0) {
    return (
      <div className="bg-gray-900 border border-gray-800 rounded-xl p-6 text-center text-gray-600">
        暂无产物，运行任务后会逐步生成
      </div>
    )
  }

  // Group artifacts by phase
  const grouped = {}
  artifacts.forEach((a) => {
    const phase = a.phase || 'other'
    if (!grouped[phase]) grouped[phase] = []
    grouped[phase].push(a)
  })

  return (
    <div className="space-y-4">
      {Object.entries(grouped).map(([phase, items]) => (
        <div key={phase} className="bg-gray-900 border border-gray-800 rounded-xl overflow-hidden">
          <div className="px-4 py-3 border-b border-gray-800">
            <span className="text-sm font-medium text-gray-300">
              {PHASE_LABELS[phase] || phase}
            </span>
          </div>
          <div className="divide-y divide-gray-800/50">
            {items.map((artifact) => (
              <div key={artifact.name}>
                <button
                  onClick={() => viewArtifact(artifact.name)}
                  className="w-full px-4 py-3 flex items-center justify-between hover:bg-gray-800/50 transition-colors text-left"
                >
                  <div className="flex items-center gap-3">
                    <span className="text-sm text-gray-200">{artifact.filename}</span>
                    <span className="text-xs text-gray-600">{formatSize(artifact.size)}</span>
                  </div>
                  <span className={`text-xs transition-transform ${expanded === artifact.name ? 'rotate-180' : ''}`}>
                    ▼
                  </span>
                </button>
                {expanded === artifact.name && (
                  <div className="px-4 pb-4">
                    {loading ? (
                      <div className="text-gray-500 text-sm">加载中...</div>
                    ) : (
                      <pre className="bg-gray-950 rounded-lg p-4 text-xs text-gray-300 overflow-x-auto max-h-96 overflow-y-auto">
                        {JSON.stringify(content, null, 2)}
                      </pre>
                    )}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  )
}
