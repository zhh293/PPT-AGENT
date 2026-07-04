import { useState, useEffect } from 'react'
import JobList from './components/JobList'
import Dashboard from './components/Dashboard'
import FileUpload from './components/FileUpload'

export default function App() {
  const [jobs, setJobs] = useState([])
  const [selectedJob, setSelectedJob] = useState(null)
  const [showUpload, setShowUpload] = useState(false)

  const fetchJobs = async () => {
    try {
      const res = await fetch('/api/jobs')
      const data = await res.json()
      setJobs(data)
    } catch (e) {
      console.error('Failed to fetch jobs:', e)
    }
  }

  useEffect(() => {
    fetchJobs()
    const interval = setInterval(fetchJobs, 5000)
    return () => clearInterval(interval)
  }, [])

  return (
    <div className="min-h-screen bg-gray-950">
      {/* Header */}
      <header className="border-b border-gray-800 bg-gray-900/50 backdrop-blur-sm sticky top-0 z-50">
        <div className="max-w-7xl mx-auto px-6 py-4 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 bg-gradient-to-br from-blue-500 to-purple-600 rounded-lg flex items-center justify-center text-white text-sm font-bold">
              P
            </div>
            <h1 className="text-lg font-semibold text-white">PPT Agent Dashboard</h1>
          </div>
          <button
            onClick={() => setShowUpload(true)}
            className="px-4 py-2 bg-blue-600 hover:bg-blue-500 text-white text-sm font-medium rounded-lg transition-colors"
          >
            + 新建任务
          </button>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-6 py-6">
        {showUpload && (
          <FileUpload
            onClose={() => setShowUpload(false)}
            onCreated={(job) => {
              setShowUpload(false)
              fetchJobs()
              setSelectedJob(job.job_id)
            }}
          />
        )}

        {selectedJob ? (
          <div>
            <button
              onClick={() => setSelectedJob(null)}
              className="mb-4 text-sm text-gray-400 hover:text-white transition-colors flex items-center gap-1"
            >
              ← 返回列表
            </button>
            <Dashboard jobId={selectedJob} />
          </div>
        ) : (
          <JobList jobs={jobs} onSelect={(id) => setSelectedJob(id)} onRefresh={fetchJobs} />
        )}
      </main>
    </div>
  )
}
