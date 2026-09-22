import { ChangeEvent, Fragment, useEffect, useMemo, useRef, useState } from 'react'
import {
  ChevronDown,
  CheckCircle2,
  Download,
  FileDown,
  FileSpreadsheet,
  Gauge,
  Info,
  Play,
  RefreshCw,
  ShieldCheck,
  TriangleAlert,
  Upload,
  X,
} from 'lucide-react'

import {
  createEvaluationJob,
  downloadEvaluationExport,
  downloadEvaluationTemplate,
  getEvaluationJob,
  getEvaluationRows,
  scoreEvaluationJob,
  updateEvaluationDisplayMetrics,
} from '../api'
import { Button } from '../components/ui/Button'
import {
  EVALUATION_METRICS,
  EvaluationJobSummary,
  EvaluationMetric,
  EvaluationRow,
  UiBootstrap,
  WorkspaceRecord,
} from '../types'

type EvaluationPageProps = {
  bootstrap: UiBootstrap
  workspace: WorkspaceRecord
}

const metricLabels: Record<EvaluationMetric, string> = {
  faithfulness: 'Bám sát nguồn',
  response_relevancy: 'Đúng trọng tâm',
  context_precision: 'Nguồn liên quan',
  context_recall: 'Đủ thông tin',
}

const metricDescriptions: Record<EvaluationMetric, string> = {
  faithfulness: 'Câu trả lời có bám vào tài liệu đã tìm thấy không.',
  response_relevancy: 'Câu trả lời có đúng trọng tâm câu hỏi không.',
  context_precision: 'Các tài liệu liên quan có được ưu tiên không.',
  context_recall: 'Tài liệu đã tìm có đủ thông tin để trả lời không.',
}

const TERMINAL_JOB_STATUSES = new Set(['completed', 'failed'])
const TERMINAL_SCORE_STATUSES = new Set([
  'completed',
  'completed_with_errors',
  'failed',
  'manual_review',
  'no_ground_truth',
  'no_scoreable_rows',
  'no_context',
])

function isEvaluationReady(job: EvaluationJobSummary): boolean {
  const finishedRows = job.completed_rows + job.failed_rows
  return TERMINAL_JOB_STATUSES.has(job.status)
    && TERMINAL_SCORE_STATUSES.has(job.score_status)
    && job.total_rows > 0
    && finishedRows === job.total_rows
}

function evaluationStatusKey(job: EvaluationJobSummary): string {
  if (job.evaluation_mode !== 'manual_review'
    && (job.score_status === 'not_started' || job.score_status === 'scoring')) {
    return 'scoring'
  }
  return job.status
}

function isEvaluationProcessing(job: EvaluationJobSummary): boolean {
  if (job.status === 'queued' || job.status === 'running') return true
  return job.evaluation_mode !== 'manual_review'
    && TERMINAL_JOB_STATUSES.has(job.status)
    && (job.score_status === 'not_started' || job.score_status === 'scoring')
}

function evaluationPhaseLabel(job: EvaluationJobSummary): string {
  if (isEvaluationProcessing(job)) {
    return TERMINAL_JOB_STATUSES.has(job.status) ? 'ĐANG CHẤM ĐIỂM' : 'ĐANG KIỂM TRA'
  }
  if (job.status === 'failed' || job.score_status === 'failed') return 'CÓ LỖI'
  return 'ĐÃ HOÀN TẤT'
}

function evaluationProgressPercent(job: EvaluationJobSummary, scoreableRows: number): number {
  const queryFinished = Math.min(job.total_rows, job.completed_rows + job.failed_rows)
  const queryProgress = job.total_rows ? queryFinished / job.total_rows : 0
  if (job.evaluation_mode === 'manual_review' || job.score_status === 'manual_review') {
    return Math.round(queryProgress * 100)
  }
  if (!TERMINAL_JOB_STATUSES.has(job.status)) return Math.round(queryProgress * 50)
  if (job.score_status === 'not_started') return 50
  if (job.score_status === 'scoring') {
    const total = Math.max(scoreableRows, job.scored_rows, 1)
    return 50 + Math.round(Math.min(1, job.scored_rows / total) * 50)
  }
  return 100
}

function evaluationProgressMessage(job: EvaluationJobSummary, scoreableRows: number): string {
  const queryFinished = Math.min(job.total_rows, job.completed_rows + job.failed_rows)
  if (job.status === 'queued') return 'Đang xếp hàng để bắt đầu kiểm tra.'
  if (job.status === 'running') return `Đang tạo câu trả lời: ${queryFinished}/${job.total_rows} dòng.`
  if (job.score_status === 'not_started') return 'Đã tạo câu trả lời. Đang chuẩn bị scoring.'
  if (job.score_status === 'scoring') {
    return `Đang scoring: ${Math.min(job.scored_rows, scoreableRows)}/${scoreableRows || '-'} câu.`
  }
  if (job.score_status === 'completed_with_errors') return 'Scoring đã xong, một số dòng cần xem lại.'
  if (job.status === 'failed' || job.score_status === 'failed') return 'Process kết thúc với lỗi. Mở từng dòng để xem chi tiết.'
  return 'Process đã hoàn tất.'
}

function exportLockMessage(job: EvaluationJobSummary, manualReview: boolean): string {
  const finishedRows = Math.min(job.total_rows, job.completed_rows + job.failed_rows)
  if (finishedRows < job.total_rows) {
    return `Đang xử lý ${finishedRows}/${job.total_rows} dòng. Chỉ tải file khi toàn bộ process đạt 100%.`
  }
  if (job.score_status === 'not_started' || job.score_status === 'scoring') {
    return 'Đang chấm điểm. Chỉ tải file khi phần trả lời và đánh giá đã hoàn tất.'
  }
  if (manualReview && !TERMINAL_SCORE_STATUSES.has(job.score_status)) {
    return 'Đang tạo câu trả lời. Chỉ tải file khi process đạt 100%.'
  }
  return 'Kết quả chưa sẵn sàng để tải. Hãy chờ trạng thái hoàn tất rồi thử lại.'
}

function hasInvalidScores(row: EvaluationRow): boolean {
  if (!row.reference_answer || row.status !== 'completed') return false
  if (row.score_error) return true
  return EVALUATION_METRICS.some((metric) => {
    const value = row.scores?.[metric]
    return typeof value !== 'number' || !Number.isFinite(value) || value < 0 || value > 1
  })
}

function invalidScoreRows(rows: EvaluationRow[]): EvaluationRow[] {
  return rows.filter(hasInvalidScores)
}

function rowStatusKey(row: EvaluationRow, job: EvaluationJobSummary): string {
  if (job.score_status === 'scoring' && hasInvalidScores(row)) {
    return 'scoring'
  }
  return row.status
}

function formatRowScore(
  row: EvaluationRow,
  metric: EvaluationMetric,
  job: EvaluationJobSummary,
): string {
  const value = row.scores?.[metric]
  if (typeof value === 'number' && Number.isFinite(value)) return value.toFixed(3)
  if (job.score_status === 'scoring' && hasInvalidScores(row)) return '...'
  if (row.score_error) return 'Lỗi'
  return '-'
}

const jobStatusLabels: Record<string, string> = {
  queued: 'Đang chờ',
  running: 'Đang trả lời',
  scoring: 'Đang chấm điểm',
  completed: 'Đã hoàn tất',
  failed: 'Có lỗi',
}

const scoreStatusLabels: Record<string, string> = {
  not_started: 'Chờ chấm điểm',
  scoring: 'Đang chấm điểm',
  completed: 'Đã chấm xong',
  completed_with_errors: 'Đã chấm, còn vài lỗi',
  failed: 'Chấm điểm thất bại',
  manual_review: 'Bạn tự xem kết quả',
  no_ground_truth: 'Không có đáp án chuẩn',
  no_scoreable_rows: 'Chưa có câu trả lời để chấm',
  no_context: 'Chưa đủ tài liệu để chấm',
}

type EvaluatorNoticePresentation = {
  tone: 'info' | 'success' | 'warning' | 'error'
  title: string
  label: string
}

function noticePresentation(message: string): EvaluatorNoticePresentation {
  if (message.startsWith('Đã tải')) return { tone: 'success', title: 'Tải xuống hoàn tất', label: 'Sẵn sàng' }
  if (message.startsWith('Không thể')) return { tone: 'error', title: 'Không thể hoàn tất', label: 'Cần thử lại' }
  if (message.includes('Vui lòng chờ') || message.includes('sau khi xử lý')) {
    return { tone: 'warning', title: 'Đang xử lý dữ liệu', label: 'Chưa sẵn sàng' }
  }
  if (message.startsWith('Đang tạo')) return { tone: 'info', title: 'Đang chuẩn bị file', label: 'Đang xử lý' }
  if (message.startsWith('File chỉ có câu hỏi')) return { tone: 'info', title: 'Đã nhận bộ câu hỏi', label: 'Tự đánh giá' }
  if (message.startsWith('File có đáp án chuẩn')) return { tone: 'success', title: 'Đã nhận bộ ground truth', label: 'Tự động chấm' }
  return { tone: 'info', title: 'Thông báo evaluation', label: 'Cập nhật' }
}

export function EvaluationPage({ bootstrap, workspace }: EvaluationPageProps) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [job, setJob] = useState<EvaluationJobSummary | null>(null)
  const [rows, setRows] = useState<EvaluationRow[]>([])
  const [displayMetrics, setDisplayMetrics] = useState<EvaluationMetric[]>([...EVALUATION_METRICS])
  const [metricDraft, setMetricDraft] = useState<EvaluationMetric[]>([...EVALUATION_METRICS])
  const [metricDialogOpen, setMetricDialogOpen] = useState(false)
  const [expandedRow, setExpandedRow] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [downloadingTemplate, setDownloadingTemplate] = useState(false)
  const [exportingFormat, setExportingFormat] = useState<'xlsx' | 'csv' | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const locale = bootstrap.locale || 'vi'

  const groundedRows = useMemo(
    () => rows.filter((row) => Boolean(row.reference_answer)),
    [rows],
  )
  const scoreEligibleRows = useMemo(
    () => groundedRows.filter((row) => row.status === 'completed' && Boolean(
      row.generated_answer?.trim() && row.trace?.final_contexts?.length,
    )),
    [groundedRows],
  )
  const isRunning = Boolean(job && isEvaluationProcessing(job))
  const manualReview = Boolean(job && (
    job.evaluation_mode === 'manual_review'
    || job.score_status === 'manual_review'
    || job.score_status === 'no_ground_truth'
  ))
  const exportReady = Boolean(job && isEvaluationReady(job))
  const noticeMeta = notice ? noticePresentation(notice) : null
  const rowsNeedingRescore = useMemo(() => invalidScoreRows(rows), [rows])
  const needsRescore = Boolean(job && !manualReview && !isRunning && rowsNeedingRescore.length)

  useEffect(() => {
    setJob(null)
    setRows([])
    setExpandedRow(null)
    setDisplayMetrics([...EVALUATION_METRICS])
    setMetricDraft([...EVALUATION_METRICS])
  }, [workspace.workspace_id])

  useEffect(() => {
    const jobId = job?.job_id
    if (!jobId) return
    let cancelled = false
    let requestInFlight = false
    const poll = async () => {
      if (cancelled || requestInFlight) return
      requestInFlight = true
      try {
        const next = await getEvaluationJob(workspace.workspace_id, jobId)
        const result = await getEvaluationRows(workspace.workspace_id, jobId, 0, 200)
        if (cancelled) return
        setJob(next)
        setRows(result.rows)
        if (next.display_metrics.length) setDisplayMetrics(next.display_metrics)
        if (result.display_metrics.length) setDisplayMetrics(result.display_metrics)
        if (isEvaluationReady(next)) window.clearInterval(interval)
      } catch (reason) {
        if (!cancelled) setError(reason instanceof Error ? reason.message : 'Không thể tải tiến độ evaluation')
      } finally {
        requestInFlight = false
      }
    }
    const interval = window.setInterval(() => void poll(), 1500)
    void poll()
    return () => {
      cancelled = true
      window.clearInterval(interval)
    }
  }, [job?.job_id, job?.score_status, workspace.workspace_id])

  useEffect(() => {
    if (!notice) return
    const timeout = window.setTimeout(() => setNotice(null), 3000)
    return () => window.clearTimeout(timeout)
  }, [notice])

  async function loadRows(nextJob: EvaluationJobSummary) {
    const result = await getEvaluationRows(workspace.workspace_id, nextJob.job_id, 0, 200)
    setRows(result.rows)
    if (result.display_metrics.length) setDisplayMetrics(result.display_metrics)
  }

  async function handleUpload(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file) return
    setBusy(true)
    setError(null)
    setRows([])
    setExpandedRow(null)
    try {
      const created = await createEvaluationJob(workspace.workspace_id, file, locale)
      setJob(created)
      const initialRows = await getEvaluationRows(workspace.workspace_id, created.job_id, 0, 200)
      setRows(initialRows.rows)
      const nextMetrics = initialRows.display_metrics.length
        ? initialRows.display_metrics
        : [...EVALUATION_METRICS]
      setDisplayMetrics(nextMetrics)
      setMetricDraft(nextMetrics)
      setMetricDialogOpen(created.evaluation_mode !== 'manual_review')
      setNotice(
        created.evaluation_mode === 'manual_review'
          ? 'File chỉ có câu hỏi. Hệ thống sẽ tạo câu trả lời để bạn tự xem và không gọi mô hình chấm điểm.'
          : 'File có đáp án chuẩn. Hệ thống sẽ tạo câu trả lời rồi tự động chấm điểm.',
      )
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Không thể upload bộ câu hỏi')
    } finally {
      setBusy(false)
    }
  }

  async function handleTemplateDownload() {
    setDownloadingTemplate(true)
    setError(null)
    try {
      await downloadEvaluationTemplate(workspace.workspace_id)
      setNotice('Đã tải file mẫu XLSX. Hãy thay dòng ví dụ bằng câu hỏi và đáp án chuẩn của bạn.')
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Không thể tải file mẫu')
    } finally {
      setDownloadingTemplate(false)
    }
  }

  function toggleMetric(metric: EvaluationMetric) {
    setMetricDraft((current) => current.includes(metric)
      ? current.filter((item) => item !== metric)
      : [...current, metric])
  }

  function openMetricDialog() {
    setMetricDraft([...displayMetrics])
    setMetricDialogOpen(true)
  }

  async function saveDisplayMetrics() {
    if (!job || !metricDraft.length) return
    setBusy(true)
    setMetricDialogOpen(false)
    setError(null)
    try {
      const accepted = await updateEvaluationDisplayMetrics(
        workspace.workspace_id,
        job.job_id,
        metricDraft,
      )
      setJob(accepted)
      setDisplayMetrics(accepted.display_metrics)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Không thể chấm điểm')
    } finally {
      setBusy(false)
    }
  }

  async function refresh() {
    if (!job) return
    setBusy(true)
    try {
      const next = await getEvaluationJob(workspace.workspace_id, job.job_id)
      setJob(next)
      await loadRows(next)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Không thể tải evaluation')
    } finally {
      setBusy(false)
    }
  }

  async function rescore() {
    if (!job || manualReview) return
    setBusy(true)
    setError(null)
    try {
      const accepted = await scoreEvaluationJob(
        workspace.workspace_id,
        job.job_id,
        [...EVALUATION_METRICS],
        [],
        true,
      )
      setJob(accepted)
      setNotice('Đã gửi yêu cầu chấm lại. Hệ thống sẽ cập nhật điểm sau khi hoàn tất.')
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Không thể chấm lại evaluation')
    } finally {
      setBusy(false)
    }
  }

  async function downloadExport(format: 'xlsx' | 'csv') {
    if (!job) return
    if (!isEvaluationReady(job)) {
      setNotice(
        job.score_status === 'scoring'
          ? 'Hệ thống vẫn đang chấm điểm. Vui lòng chờ trạng thái hoàn tất rồi tải file.'
          : manualReview
            ? 'Hệ thống vẫn đang tạo câu trả lời. Bạn có thể tải file sau khi xử lý xong.'
            : 'Hệ thống vẫn đang tạo câu trả lời và chấm điểm. Vui lòng chờ hoàn tất.',
      )
      return
    }
    setError(null)
    setExportingFormat(format)
    setNotice(`Đang tạo file ${format === 'csv' ? 'ZIP CSV' : 'XLSX'}...`)
    try {
      await downloadEvaluationExport(workspace.workspace_id, job.job_id, format)
      setNotice(`Đã tải file ${format === 'csv' ? 'ZIP CSV' : 'XLSX'}.`)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Không thể tải file evaluation')
      setNotice('Không thể tạo file tải xuống. Vui lòng thử lại sau.')
    } finally {
      setExportingFormat(null)
    }
  }

  return (
    <section className="workspace-evaluator" aria-labelledby="evaluator-title">
      <header className="evaluator-heading">
        <div>
          <span className="evaluator-eyebrow"><Gauge size={14} /> KIỂM TRA CHẤT LƯỢNG</span>
          <h2 id="evaluator-title">Kiểm tra nhiều câu hỏi</h2>
          <p>Chạy nhiều câu hỏi cùng lúc để xem trợ lý trả lời đúng và đủ đến đâu.</p>
        </div>
        <div className="evaluator-model-badge">
          <ShieldCheck size={16} />
          <span>
            {manualReview ? 'Cách đánh giá' : job ? 'Mô hình chấm điểm' : 'Chế độ kiểm tra'}
            <strong>{manualReview ? 'Bạn tự xem kết quả' : job?.judge_model || (job ? 'Đang chuẩn bị' : 'Tự động nhận diện')}</strong>
          </span>
        </div>
      </header>

      <div className="evaluator-upload-card">
        <div className="evaluator-upload-icon"><FileSpreadsheet /></div>
        <div>
          <strong>Tải lên danh sách câu hỏi</strong>
          <span>Hỗ trợ CSV, XLSX · cột câu hỏi là bắt buộc · đáp án chuẩn là tùy chọn · tối đa 200 dòng</span>
        </div>
        <div className="evaluator-upload-actions">
          <Button variant="ghost" leadingIcon={<FileDown />} loading={downloadingTemplate} onClick={() => void handleTemplateDownload()}>
            Tải file mẫu
          </Button>
          <Button variant="primary" leadingIcon={<Upload />} onClick={() => inputRef.current?.click()} disabled={busy || isRunning}>
            {busy ? 'Đang xử lý...' : 'Chọn file'}
          </Button>
        </div>
        <input ref={inputRef} className="sr-only" type="file" accept=".csv,.xlsx" onChange={(event) => void handleUpload(event)} />
      </div>

      {error && <div className="evaluator-alert" role="alert">{error}</div>}
      {notice && noticeMeta && (
        <div className={`evaluator-toast evaluator-toast--${noticeMeta.tone}`} role="status" aria-live="polite">
          <div className="evaluator-toast-icon" aria-hidden="true">
            {noticeMeta.tone === 'success' && <CheckCircle2 size={18} />}
            {noticeMeta.tone === 'warning' && <TriangleAlert size={18} />}
            {noticeMeta.tone === 'error' && <TriangleAlert size={18} />}
            {noticeMeta.tone === 'info' && <Info size={18} />}
          </div>
          <div className="evaluator-toast-content">
            <div className="evaluator-toast-heading">
              <strong>{noticeMeta.title}</strong>
              <span>{noticeMeta.label}</span>
            </div>
            <p>{notice}</p>
          </div>
          <button type="button" aria-label="Đóng thông báo" onClick={() => setNotice(null)}><X size={15} /></button>
          <i className="evaluator-toast-progress" aria-hidden="true" />
        </div>
      )}

      {job && (
        <section className="evaluator-job-card">
          <div className="evaluator-job-topline">
            <div><span>{evaluationPhaseLabel(job)}</span><strong>{job.filename}</strong></div>
            <div className="evaluator-job-actions">
              <span className={`evaluator-status evaluator-status--${evaluationStatusKey(job)}`}>{jobStatusLabels[evaluationStatusKey(job)] || evaluationStatusKey(job)}</span>
              {needsRescore && <Button variant="ghost" size="sm" disabled={busy} onClick={() => void rescore()}>Chấm lại {rowsNeedingRescore.length} dòng lỗi</Button>}
              <Button variant="ghost" size="icon" aria-label="Refresh" onClick={() => void refresh()} leadingIcon={<RefreshCw size={15} />} />
            </div>
          </div>
          <div className="evaluator-progress-track"><span style={{ width: `${evaluationProgressPercent(job, scoreEligibleRows.length)}%` }} /></div>
          <p className="evaluator-progress-message" role="status" aria-live="polite">{evaluationProgressMessage(job, scoreEligibleRows.length)}</p>
          <div className="evaluator-job-stats">
            <span><strong>{job.completed_rows}</strong>/{job.total_rows} dòng</span>
            {manualReview
              ? <span><strong>{job.completed_rows}</strong> câu trả lời để xem</span>
              : <span><strong>{scoreEligibleRows.length}</strong> câu sẵn sàng chấm</span>}
            {!manualReview && <span><strong>{job.scored_rows}</strong> câu đã chấm</span>}
            <span>Đánh giá: <strong>{scoreStatusLabels[job.score_status] || job.score_status}</strong></span>
            {job.judge_model && <span>mô hình: <strong>{job.judge_model}</strong></span>}
          </div>
          <div className="evaluator-job-buttons">
            {!manualReview && (
              <Button variant="secondary" leadingIcon={<Gauge size={15} />} disabled={busy} onClick={openMetricDialog}>
                Chọn nội dung báo cáo
              </Button>
            )}
            <Button variant="ghost" leadingIcon={<Download size={15} />} disabled={!exportReady || Boolean(exportingFormat)} title={!exportReady ? exportLockMessage(job, manualReview) : 'Tải file XLSX'} onClick={() => void downloadExport('xlsx')}>{exportingFormat === 'xlsx' ? 'Đang tạo...' : 'XLSX'}</Button>
            <Button variant="ghost" leadingIcon={<Download size={15} />} disabled={!exportReady || Boolean(exportingFormat)} title={!exportReady ? exportLockMessage(job, manualReview) : 'Tải ZIP CSV'} onClick={() => void downloadExport('csv')}>{exportingFormat === 'csv' ? 'Đang tạo...' : 'Tải ZIP CSV'}</Button>
          </div>
          {!exportReady && <p className="evaluator-export-note" role="status">{exportLockMessage(job, manualReview)}</p>}
          {manualReview && <p className="evaluator-score-note">File chỉ có câu hỏi. Hệ thống tạo câu trả lời và dừng lại để bạn tự đánh giá, không gọi thêm mô hình chấm điểm.</p>}
          {job.score_status === 'no_scoreable_rows' && <p className="evaluator-score-note">File có đáp án chuẩn nhưng chưa có dòng query hoàn tất để chấm. Kiểm tra lỗi query ở từng dòng rồi chạy lại.</p>}
          {job.score_status === 'no_context' && <p className="evaluator-score-note">Đã có đáp án chuẩn nhưng chưa có tài liệu hoặc câu trả lời để chấm. Hãy kiểm tra từng dòng.</p>}
          {job.score_status === 'completed_with_errors' && <p className="evaluator-score-note">Một số dòng chấm điểm bị lỗi; mở từng dòng để xem chi tiết.</p>}
          {needsRescore && <p className="evaluator-score-note">Phát hiện {rowsNeedingRescore.length} dòng có điểm thiếu hoặc không hợp lệ. Chỉ các dòng này sẽ được chấm lại; kết quả hợp lệ được giữ nguyên.</p>}
        </section>
      )}

      {job && rows.length > 0 && (
        <section className="evaluator-results-card">
          <div className="evaluator-results-heading">
            <div><span>{isRunning ? 'PROCESS ĐANG CHẠY' : 'KẾT QUẢ'}</span><h3>{isRunning ? 'Theo dõi câu trả lời' : 'Xem lại câu trả lời'}</h3></div>
            {!manualReview && <div className="evaluator-selected-metrics">{displayMetrics.map((metric) => <span key={metric}>{metricLabels[metric]}</span>)}</div>}
          </div>
          {isRunning && <p className="evaluator-results-live-note" role="status">Rows được cập nhật tự động. Điểm chỉ được xem là hoàn tất sau khi scoring kết thúc.</p>}
          <div className="evaluator-table-wrap">
            <table className="evaluator-table">
              <thead><tr><th>Mã câu hỏi</th><th>Câu hỏi</th><th>Câu trả lời của trợ lý</th>{!manualReview && <th>Đáp án chuẩn</th>}<th>Trạng thái</th>{!manualReview && displayMetrics.map((metric) => <th key={metric}>{metricLabels[metric]}</th>)}<th /></tr></thead>
              <tbody>
                {rows.map((row) => {
                  const expanded = expandedRow === row.case_id
                  return (
                    <Fragment key={row.case_id}>
                      <tr key={row.case_id} className={expanded ? 'is-expanded' : ''}>
                        <td><code>{row.case_id}</code></td>
                        <td className="evaluator-question">{row.question}</td>
                        <td className="evaluator-answer">{row.generated_answer || row.error_message || row.score_error || '-'}</td>
                        {!manualReview && <td className="evaluator-answer">{row.reference_answer || '-'}</td>}
                        <td><span className={`evaluator-row-status evaluator-row-status--${rowStatusKey(row, job)}`}>{jobStatusLabels[rowStatusKey(row, job)] || rowStatusKey(row, job)}</span></td>
                        {!manualReview && displayMetrics.map((metric) => <td key={metric} className="evaluator-score">{formatRowScore(row, metric, job)}</td>)}
                        <td><Button variant="ghost" size="icon" aria-label="Xem chi tiết tìm kiếm" onClick={() => setExpandedRow(expanded ? null : row.case_id)} leadingIcon={<ChevronDown className={expanded ? 'is-rotated' : ''} size={15} />} /></td>
                      </tr>
                      {expanded && <tr key={`${row.case_id}-trace`} className="evaluator-trace-row"><td colSpan={manualReview ? 5 : 6 + displayMetrics.length}><TraceInspector row={row} /></td></tr>}
                    </Fragment>
                  )
                })}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {metricDialogOpen && job && !manualReview && (
        <div className="evaluator-dialog-backdrop" role="presentation">
          <section className="evaluator-dialog" role="dialog" aria-modal="true" aria-labelledby="metric-dialog-title">
            <Button variant="ghost" size="icon" className="evaluator-dialog-close" aria-label="Đóng" onClick={() => setMetricDialogOpen(false)} leadingIcon={<X size={17} />} />
            <span className="evaluator-eyebrow">CHỌN NỘI DUNG HIỂN THỊ</span>
            <h3 id="metric-dialog-title">Chọn tiêu chí đánh giá</h3>
            <p>Hệ thống luôn tính đủ bốn tiêu chí. Chọn tiêu chí bạn muốn xem trên bảng và file báo cáo.</p>
            <div className="evaluator-groundtruth-note">{groundedRows.length}/{rows.length || job.total_rows} dòng có đáp án chuẩn; {scoreEligibleRows.length} dòng đã có câu trả lời và tài liệu để chấm.</div>
            <div className="evaluator-metric-actions"><Button variant="ghost" size="sm" onClick={() => setMetricDraft([...EVALUATION_METRICS])}>Chọn tất cả</Button><Button variant="ghost" size="sm" onClick={() => setMetricDraft([])}>Bỏ chọn tất cả</Button></div>
            <div className="evaluator-metric-options">
              {EVALUATION_METRICS.map((metric) => {
                const checked = metricDraft.includes(metric)
                return <label key={metric} className={`evaluator-metric-option ${checked ? 'is-checked' : ''}`}><input type="checkbox" checked={checked} onChange={() => toggleMetric(metric)} /><span><strong>{metricLabels[metric]}</strong><small>{metricDescriptions[metric]}</small></span></label>
              })}
            </div>
            <Button variant="primary" fullWidth leadingIcon={<Play size={15} />} disabled={!metricDraft.length || busy} onClick={() => void saveDisplayMetrics()}>Lưu tiêu chí hiển thị</Button>
          </section>
        </div>
      )}
    </section>
  )
}

function TraceInspector({ row }: { row: EvaluationRow }) {
  const trace = row.trace
  if (!trace) return <div className="evaluator-trace-empty">Chưa có chi tiết tìm kiếm.</div>
  const stages = [
    ['Kho dữ liệu', trace.vector_db],
    ['Tìm kiếm kết hợp', trace.hybrid_retrieval],
    ['Tìm theo mối liên hệ', trace.graph_search],
    ['Sắp xếp lại kết quả', trace.reranking],
    ['Nội dung dùng để trả lời', trace.final_contexts],
  ] as const
  const availability = trace.availability || {}
  const availabilityKeys: Record<string, string> = {
    'Kho dữ liệu': 'vector_db',
    'Tìm kiếm kết hợp': 'hybrid_retrieval',
    'Tìm theo mối liên hệ': 'graph_search',
    'Sắp xếp lại kết quả': 'reranking',
    'Nội dung dùng để trả lời': 'final_contexts',
  }
  return <div className="evaluator-trace-inspector"><div className="evaluator-trace-meta"><span>Mã câu trả lời: <code>{row.answer_id || '-'}</code></span><span>Thời gian: {row.duration_ms == null ? '-' : `${row.duration_ms}ms`}</span><span>Trạng thái tìm kiếm: {trace.trace_error || 'Bình thường'}</span></div><div className="evaluator-stage-grid">{stages.map(([name, items]) => <article key={name}><strong>{name}</strong><span>{items.length} đoạn nội dung · {availability[availabilityKeys[name]] === 'available' || (!availability[availabilityKeys[name]] && items.length) ? 'có dữ liệu' : 'chưa có thông tin'}</span>{items.slice(0, 3).map((item, index) => <p key={`${name}-${item.chunk_id || index}`}><b>#{item.rank || index + 1}</b> {item.content || item.source_path || 'Không có nội dung'}</p>)}</article>)}</div></div>
}
