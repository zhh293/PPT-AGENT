const STATUS_COLORS = {
  idle: 'bg-gray-700 text-gray-300',
  running: 'bg-blue-900/50 text-blue-300 border border-blue-700',
  completed: 'bg-green-900/50 text-green-300 border border-green-700',
  failed: 'bg-red-900/50 text-red-300 border border-red-700',
}

export default function JobList({ jobs, onSelect, onRefresh }) {
  if (jobs.length === 0) {
    return (
      <div className="text-center py-20">
        <div className="text-gray-500 text-lg mb-2">暂无任务</div>
        <p className="text-gray-600 text-sm">点击右上角"+ 新建任务"开始生成 PPT</p>
      </div>
    )
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-lg font-medium text-white">任务列表</h2>
        <button
          onClick={onRefresh}
          className="text-sm text-gray-400 hover:text-white transition-colors"
        >
          刷新
        </button>
      </div>
      {jobs.map((job) => (
        <div
          key={job.job_id}
          onClick={() => onSelect(job.job_id)}
          className="bg-gray-900 border border-gray-800 rounded-xl p-4 hover:border-gray-600 cursor-pointer transition-all group"
        >
          <div className="flex items-center justify-between">
            <div>
              <div className="font-medium text-white group-hover:text-blue-400 transition-colors">
                {job.job_id}
              </div>
              {job.created_at && (
                <div className="text-xs text-gray-500 mt-1">
                  {new Date(job.created_at).toLocaleString('zh-CN')}
                </div>
              )}
            </div>
            <div className="flex items-center gap-3">
              <span className={`px-2 py-1 text-xs font-medium rounded-md ${STATUS_COLORS[job.status] || STATUS_COLORS.idle}`}>
                {job.status}
              </span>
              <div className="text-xs text-gray-500">
                {job.phases_completed.length}/8 阶段
              </div>
            </div>
          </div>
          {/* Phase progress bar */}
          <div className="mt-3 flex gap-1">
            {['document_analysis', 'outline_generation', 'template_matching', 'design_planning', 'content_mapping', 'visual_generation', 'ppt_assembly', 'verification'].map((phase) => (
              <div
                key={phase}
                className={`h-1.5 flex-1 rounded-full transition-colors ${
                  job.phases_completed.includes(phase)
                    ? 'bg-green-500'
                    : phase === job.current_phase
                    ? 'bg-blue-500 animate-pulse'
                    : 'bg-gray-800'
                }`}
              />
            ))}
          </div>
        </div>
      ))}
    </div>
  )
}
