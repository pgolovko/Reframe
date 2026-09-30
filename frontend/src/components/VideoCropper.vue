<script setup lang="ts">
import { ref, nextTick, onMounted, onUnmounted, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { fileToBase64, base64ToFile } from '../utils/config'

const props = defineProps<{
  videoUrl: string
  videoFile: File
  isBatchMode?: boolean
}>()

const emit = defineEmits<{
  (e: 'reset'): void
}>()

const { t } = useI18n()

const presets = [
  { id: 'tiktok', ratio: 9/16 },
  { id: 'insta_feed', ratio: 1/1 },
  { id: 'insta_portrait', ratio: 4/5 },
  { id: 'custom', ratio: 0 },
]

const customRatioW = ref(16)
const customRatioH = ref(9)

watch([customRatioW, customRatioH], () => {
  if (selectedPreset.value.id === 'custom') {
    initializeCropBox()
  }
})

const selectedPreset = ref(presets[0])
const videoRef = ref<HTMLVideoElement | null>(null)
const containerRef = ref<HTMLDivElement | null>(null)

// Crop Box State
const boxLeft = ref(0)
const boxTop = ref(0)
const boxWidth = ref(0)
const boxHeight = ref(0)
const isDragging = ref(false)
const dragStartX = ref(0)
const dragStartY = ref(0)
const dragStartLeft = ref(0)
const dragStartTop = ref(0)

// Video Boundaries
const displayedW = ref(0)
const displayedH = ref(0)
const offsetX = ref(0)
const offsetY = ref(0)

// Trimming State
const videoDuration = ref(0)
const trimStart = ref(0)
const trimEnd = ref(0)

watch([trimStart, trimEnd], ([newStart, newEnd]) => {
  if (newStart < 0) trimStart.value = 0
  if (newEnd > videoDuration.value) trimEnd.value = videoDuration.value
  
  if (newStart > newEnd) {
    // Prevent overlapping
    trimStart.value = newEnd - 0.1
  }
  
  if (videoRef.value && Math.abs(videoRef.value.currentTime - newStart) > 0.5) {
     videoRef.value.currentTime = newStart
  }
})

const isExporting = ref(false)
const progress = ref(0)
const downloadUrl = ref<string | null>(null)
const downloadFilename = ref("reframe-export.mp4")
const latestLogText = ref('Exporting...')
const logs = ref<string[]>([])
const showLogs = ref(false)

// Quality Options
const quality = ref('high')
const muteAudio = ref(false)
const useGpu = ref(false)
const outputFormat = ref('mp4')

// Overlay State
const showAdvancedOptions = ref(false)

const watermarks = ref<any[]>([])
const handleLogoUpload = (e: Event) => {
  const target = e.target as HTMLInputElement
  if (target.files && target.files.length > 0) {
    Array.from(target.files).forEach(file => {
      watermarks.value.push({
        file: file,
        url: URL.createObjectURL(file),
        x: 10,
        y: 10,
        rotation: 0,
        scale: 1,
        opacity: 100
      })
    })
  }
  target.value = ''
}
const removeWatermark = (index: number) => {
  URL.revokeObjectURL(watermarks.value[index].url)
  watermarks.value.splice(index, 1)
}

const texts = ref<any[]>([])
const addText = () => {
  texts.value.push({
    content: '',
    font: fonts.value.length > 0 ? fonts.value[0] : 'Fira Code',
    color: '#ffffff',
    shadow: true,
    x: 50,
    y: 50,
    rotation: 0,
    scale: 1,
    opacity: 100
  })
}
const removeText = (index: number) => {
  texts.value.splice(index, 1)
}

const subtitleEnabled = ref(false)
const subtitleFont = ref('Fira Code')
const subtitleFontSize = ref(32)
const subtitleColor = ref('#ffffff')
const subtitleHighlight = ref('#ffff00')
const subtitleStroke = ref('#000000')

const subtitleX = ref(50)
const subtitleY = ref(50)
const subtitleW = ref(300)
const subtitleH = ref(80)

const isSubtitleDragging = ref(false)
const subtitleDragStartX = ref(0)
const subtitleDragStartY = ref(0)
const subtitleDragStartLeft = ref(0)
const subtitleDragStartTop = ref(0)

const subtitleOverlayRef = ref<HTMLElement | null>(null)
let subtitleObserver: ResizeObserver | null = null

const startSubtitleDrag = (e: MouseEvent) => {
  e.stopPropagation()
  isSubtitleDragging.value = true
  subtitleDragStartX.value = e.clientX
  subtitleDragStartY.value = e.clientY
  subtitleDragStartLeft.value = subtitleX.value
  subtitleDragStartTop.value = subtitleY.value
  window.addEventListener('mousemove', onSubtitleDrag)
  window.addEventListener('mouseup', endSubtitleDrag)
}

const onSubtitleDrag = (e: MouseEvent) => {
  if (!isSubtitleDragging.value) return
  const dx = e.clientX - subtitleDragStartX.value
  const dy = e.clientY - subtitleDragStartY.value
  subtitleX.value = subtitleDragStartLeft.value + dx
  subtitleY.value = subtitleDragStartTop.value + dy
}

const endSubtitleDrag = () => {
  isSubtitleDragging.value = false
  window.removeEventListener('mousemove', onSubtitleDrag)
  window.removeEventListener('mouseup', endSubtitleDrag)
}

const fonts = ref<string[]>([
  'Fira Code', 'Inter', 'Arial', 'Verdana', 'Tahoma', 'Trebuchet MS', 
  'Times New Roman', 'Georgia', 'Garamond', 'Courier New', 
  'Brush Script MT', 'Comic Sans MS', 'Impact', 'Palatino Linotype', 
  'Lucida Sans Unicode'
])

const fetchFonts = async () => {
  try {
    const response = await fetch('/api/fonts')
    const data = await response.json()
    if (data.static || data.custom) {
      const newFonts = [...(data.static || []), ...(data.custom || [])]
      fonts.value = newFonts
      
      // Load custom fonts into the browser so the canvas can render them
      for (const fontName of data.custom || []) {
        const isLoaded = Array.from(document.fonts).some(f => f.family === fontName)
        if (!isLoaded) {
          try {
            const font = new FontFace(fontName, `url(/api/fonts/${encodeURIComponent(fontName)}/file)`)
            await font.load()
            document.fonts.add(font)
          } catch (err) {
            console.error(`Failed to load font ${fontName}:`, err)
          }
        }
      }
    }
  } catch (error) {
    console.error('Error fetching fonts:', error)
  }
}

// Calculate crop box size based on preset and video aspect ratio
const initializeCropBox = () => {
  if (!videoRef.value || !containerRef.value) return
  
  const videoW = videoRef.value.videoWidth
  const videoH = videoRef.value.videoHeight
  const containerW = containerRef.value.clientWidth
  const containerH = containerRef.value.clientHeight
  
  if (videoW === 0 || videoH === 0) return

  // Displayed video dimensions (assuming object-fit: contain)
  const videoRatio = videoW / videoH
  const containerRatio = containerW / containerH
  
  let displayedWLocal, displayedHLocal
  
  if (containerRatio > videoRatio) {
    displayedHLocal = containerH
    displayedWLocal = containerH * videoRatio
    offsetX.value = (containerW - displayedWLocal) / 2
    offsetY.value = 0
  } else {
    displayedWLocal = containerW
    displayedHLocal = containerW / videoRatio
    offsetX.value = 0
    offsetY.value = (containerH - displayedHLocal) / 2
  }
  
  displayedW.value = displayedWLocal
  displayedH.value = displayedHLocal
  
  // Target ratio box
  const targetRatio = selectedPreset.value.id === 'custom' 
    ? (customRatioW.value / customRatioH.value || 1) 
    : selectedPreset.value.ratio
  let bw, bh
  if (targetRatio > videoRatio) {
    bw = displayedWLocal
    bh = displayedWLocal / targetRatio
  } else {
    bh = displayedHLocal
    bw = displayedHLocal * targetRatio
  }
  
  boxWidth.value = bw
  boxHeight.value = bh
  
  // Center box
  boxLeft.value = (containerW - bw) / 2
  boxTop.value = (containerH - bh) / 2
}

const onVideoLoaded = () => {
  if (videoRef.value) {
    videoDuration.value = videoRef.value.duration
    trimEnd.value = videoRef.value.duration
  }
  initializeCropBox()
}

const selectPreset = (preset: any) => {
  selectedPreset.value = preset
  initializeCropBox()
}

// Drag logic
const hasMoved = ref(false)

const startDrag = (e: MouseEvent) => {
  isDragging.value = true
  hasMoved.value = false
  dragStartX.value = e.clientX
  dragStartY.value = e.clientY
  dragStartLeft.value = boxLeft.value
  dragStartTop.value = boxTop.value
  window.addEventListener('mousemove', onDrag)
  window.addEventListener('mouseup', endDrag)
}

const onDrag = (e: MouseEvent) => {
  if (!isDragging.value) return
  const dx = e.clientX - dragStartX.value
  const dy = e.clientY - dragStartY.value
  
  if (Math.abs(dx) > 2 || Math.abs(dy) > 2) {
    hasMoved.value = true
  }
  
  if (!containerRef.value) return
  
  let newLeft = dragStartLeft.value + dx
  let newTop = dragStartTop.value + dy
  
  // Constrain bounds to actual video display area
  newLeft = Math.max(offsetX.value, Math.min(newLeft, offsetX.value + displayedW.value - boxWidth.value))
  newTop = Math.max(offsetY.value, Math.min(newTop, offsetY.value + displayedH.value - boxHeight.value))
  
  boxLeft.value = newLeft
  boxTop.value = newTop
}

const endDrag = () => {
  if (!hasMoved.value && videoRef.value) {
    if (videoRef.value.paused) {
      videoRef.value.play()
    } else {
      videoRef.value.pause()
    }
  }
  isDragging.value = false
  window.removeEventListener('mousemove', onDrag)
  window.removeEventListener('mouseup', endDrag)
}

// Drag Logic
const activeIndex = ref(-1)

const isLogoDragging = ref(false)
const logoDragStartX = ref(0)
const logoDragStartY = ref(0)
const logoDragStartLeft = ref(0)
const logoDragStartTop = ref(0)

const startLogoDrag = (e: MouseEvent, index: number) => {
  e.stopPropagation()
  activeIndex.value = index
  isLogoDragging.value = true
  logoDragStartX.value = e.clientX
  logoDragStartY.value = e.clientY
  logoDragStartLeft.value = watermarks.value[index].x
  logoDragStartTop.value = watermarks.value[index].y
  window.addEventListener('mousemove', onLogoDrag)
  window.addEventListener('mouseup', endLogoDrag)
}

const onLogoDrag = (e: MouseEvent) => {
  if (!isLogoDragging.value || activeIndex.value < 0) return
  const dx = e.clientX - logoDragStartX.value
  const dy = e.clientY - logoDragStartY.value
  watermarks.value[activeIndex.value].x = logoDragStartLeft.value + dx
  watermarks.value[activeIndex.value].y = logoDragStartTop.value + dy
}

const endLogoDrag = () => {
  isLogoDragging.value = false
  activeIndex.value = -1
  window.removeEventListener('mousemove', onLogoDrag)
  window.removeEventListener('mouseup', endLogoDrag)
}

const isTextDragging = ref(false)
const textDragStartX = ref(0)
const textDragStartY = ref(0)
const textDragStartLeft = ref(0)
const textDragStartTop = ref(0)

const startTextDrag = (e: MouseEvent, index: number) => {
  e.stopPropagation()
  activeIndex.value = index
  isTextDragging.value = true
  textDragStartX.value = e.clientX
  textDragStartY.value = e.clientY
  textDragStartLeft.value = texts.value[index].x
  textDragStartTop.value = texts.value[index].y
  window.addEventListener('mousemove', onTextDrag)
  window.addEventListener('mouseup', endTextDrag)
}

const onTextDrag = (e: MouseEvent) => {
  if (!isTextDragging.value || activeIndex.value < 0) return
  const dx = e.clientX - textDragStartX.value
  const dy = e.clientY - textDragStartY.value
  texts.value[activeIndex.value].x = textDragStartLeft.value + dx
  texts.value[activeIndex.value].y = textDragStartTop.value + dy
}

const endTextDrag = () => {
  isTextDragging.value = false
  activeIndex.value = -1
  window.removeEventListener('mousemove', onTextDrag)
  window.removeEventListener('mouseup', endTextDrag)
}

// Rotate Logic
const isRotating = ref(false)
let rotationCenter = { x: 0, y: 0 }
let startAngle = 0
let startRotation = 0

const startRotate = (e: MouseEvent, index: number) => {
  e.stopPropagation()
  activeIndex.value = index
  isRotating.value = true
  
  const logoEl = document.getElementById(`logo-overlay-${index}`)
  if (logoEl) {
    const rect = logoEl.getBoundingClientRect()
    rotationCenter = {
      x: rect.left + rect.width / 2,
      y: rect.top + rect.height / 2
    }
  }
  
  startAngle = Math.atan2(e.clientY - rotationCenter.y, e.clientX - rotationCenter.x)
  startRotation = watermarks.value[index].rotation
  
  window.addEventListener('mousemove', onRotate)
  window.addEventListener('mouseup', endRotate)
}

const onRotate = (e: MouseEvent) => {
  if (!isRotating.value || activeIndex.value < 0) return
  const currentAngle = Math.atan2(e.clientY - rotationCenter.y, e.clientX - rotationCenter.x)
  let angleDiff = (currentAngle - startAngle) * (180 / Math.PI)
  watermarks.value[activeIndex.value].rotation = startRotation + angleDiff
}

const endRotate = () => {
  isRotating.value = false
  activeIndex.value = -1
  window.removeEventListener('mousemove', onRotate)
  window.removeEventListener('mouseup', endRotate)
}

const isTextRotating = ref(false)
let textRotationCenter = { x: 0, y: 0 }
let textStartAngle = 0
let textStartRotation = 0

const startTextRotate = (e: MouseEvent, index: number) => {
  e.stopPropagation()
  activeIndex.value = index
  isTextRotating.value = true
  
  const textEl = document.getElementById(`text-overlay-${index}`)
  if (textEl) {
    const rect = textEl.getBoundingClientRect()
    textRotationCenter = {
      x: rect.left + rect.width / 2,
      y: rect.top + rect.height / 2
    }
  }
  
  textStartAngle = Math.atan2(e.clientY - textRotationCenter.y, e.clientX - textRotationCenter.x)
  textStartRotation = texts.value[index].rotation
  
  window.addEventListener('mousemove', onTextRotate)
  window.addEventListener('mouseup', endTextRotate)
}

const onTextRotate = (e: MouseEvent) => {
  if (!isTextRotating.value || activeIndex.value < 0) return
  const currentAngle = Math.atan2(e.clientY - textRotationCenter.y, e.clientX - textRotationCenter.x)
  let angleDiff = (currentAngle - textStartAngle) * (180 / Math.PI)
  texts.value[activeIndex.value].rotation = textStartRotation + angleDiff
}

const endTextRotate = () => {
  isTextRotating.value = false
  activeIndex.value = -1
  window.removeEventListener('mousemove', onTextRotate)
  window.removeEventListener('mouseup', endTextRotate)
}

const createTextFile = async (textEl: HTMLElement, textItem: any): Promise<File> => {
  const canvas = document.createElement('canvas')
  const ctx = canvas.getContext('2d')!
  
  const renderScale = 4
  canvas.width = textEl.offsetWidth * renderScale
  canvas.height = textEl.offsetHeight * renderScale
  
  const fontSize = (24 * textItem.scale) * renderScale
  
  ctx.font = `bold ${fontSize}px "${textItem.font}"`
  ctx.textBaseline = 'top'
  ctx.textAlign = 'center'
  
  if (textItem.shadow) {
    ctx.shadowColor = 'rgba(0,0,0,0.8)'
    ctx.shadowBlur = 4 * renderScale
    ctx.shadowOffsetX = 2 * renderScale
    ctx.shadowOffsetY = 2 * renderScale
  }
  
  if (textItem.opacity !== undefined) {
    ctx.globalAlpha = textItem.opacity / 100
  }
  
  ctx.fillStyle = textItem.color
  const lines = textItem.content.split('\n')
  const lineHeight = fontSize * 1.2
  
  const paddingTop = 10 * renderScale
  
  lines.forEach((line: string, i: number) => {
    ctx.fillText(line, canvas.width / 2, paddingTop + i * lineHeight)
  })
  
  return new Promise((resolve) => {
    canvas.toBlob((blob) => {
      resolve(new File([blob!], 'text.png', { type: 'image/png' }))
    }, 'image/png')
  })
}

const exportVideo = async (): Promise<void> => {
  if (!videoRef.value || !containerRef.value) return
  isExporting.value = true
  progress.value = 0
  logs.value = []
  latestLogText.value = 'Initializing...'
  
  const videoW = videoRef.value.videoWidth
  const videoH = videoRef.value.videoHeight
  
  const scale = videoW / displayedW.value
  
  let realX = Math.round((boxLeft.value - offsetX.value) * scale)
  let realY = Math.round((boxTop.value - offsetY.value) * scale)
  let realW = Math.round(boxWidth.value * scale)
  let realH = Math.round(boxHeight.value * scale)
  
  realX = Math.max(0, Math.min(realX, videoW - realW))
  realY = Math.max(0, Math.min(realY, videoH - realH))
  
  isExporting.value = true
  progress.value = 0
  downloadUrl.value = null
  
  const origName = props.videoFile.name.substring(0, props.videoFile.name.lastIndexOf('.')) || props.videoFile.name;
  const ratio = selectedPreset.value.id === 'custom' 
    ? `${customRatioW.value}x${customRatioH.value}`
    : t('cropper.presets.' + selectedPreset.value.id).split(' ')[0].replace(':', 'x');
  const fileExt = outputFormat.value === 'gif' ? 'gif' : 'mp4'
  const customFileName = `reframe_${origName}_${ratio}.${fileExt}`;
  
  const formData = new FormData()
  formData.append('file', props.videoFile)
  formData.append('customFilename', customFileName)
  formData.append('x', realX.toString())
  formData.append('y', realY.toString())
  formData.append('width', realW.toString())
  formData.append('height', realH.toString())
  formData.append('trimStart', trimStart.value.toString())
  formData.append('trimEnd', trimEnd.value.toString())
  formData.append('quality', quality.value)
  formData.append('muteAudio', muteAudio.value.toString())
  formData.append('useGpu', useGpu.value.toString())
  formData.append('outputFormat', outputFormat.value)
  
  formData.append('subtitleEnabled', subtitleEnabled.value ? "true" : "false")
  formData.append('subtitleModel', localStorage.getItem('reframe_subtitle_model') || 'base')
  formData.append('subtitleFont', subtitleFont.value)
  formData.append('subtitleFontSize', Math.round(subtitleFontSize.value * scale).toString())
  formData.append('subtitleColor', subtitleColor.value)
  formData.append('subtitleHighlight', subtitleHighlight.value)
  formData.append('subtitleStroke', subtitleStroke.value)
  formData.append('subtitleX', Math.round(subtitleX.value * scale).toString())
  formData.append('subtitleY', Math.round(subtitleY.value * scale).toString())
  formData.append('subtitleW', Math.round(subtitleW.value * scale).toString())
  formData.append('subtitleH', Math.round(subtitleH.value * scale).toString())

  watermarks.value.forEach((wm, index) => {
    const baseLogoW = 150
    const scaledLogoW = baseLogoW * wm.scale
    
    const logoImg = document.getElementById(`logo-img-${index}`) as HTMLImageElement;
    const logoRatio = logoImg && logoImg.naturalHeight ? (logoImg.naturalWidth / logoImg.naturalHeight) : 1;
    const scaledLogoH = scaledLogoW / logoRatio;
    
    const realLogoW = Math.round(scaledLogoW * scale)
    const realLogoH = Math.round(scaledLogoH * scale)
    const realLogoX = Math.round(wm.x * scale)
    const realLogoY = Math.round(wm.y * scale)
    
    formData.append('logoFiles', wm.file)
    formData.append('logoXs', realLogoX.toString())
    formData.append('logoYs', realLogoY.toString())
    formData.append('logoWs', realLogoW.toString())
    formData.append('logoHs', realLogoH.toString())
    formData.append('logoRotations', wm.rotation.toString())
    formData.append('logoOpacities', wm.opacity.toString())
  })
  
  for (let i = 0; i < texts.value.length; i++) {
    const textItem = texts.value[i]
    if (!textItem.content) continue
    
    const textEl = document.getElementById(`text-overlay-${i}`)
    if (textEl) {
      const textFile = await createTextFile(textEl, textItem)
      const realTextW = Math.round(textEl.offsetWidth * scale)
      const realTextH = Math.round(textEl.offsetHeight * scale)
      const realTextX = Math.round(textItem.x * scale)
      const realTextY = Math.round(textItem.y * scale)
      
      formData.append('textFiles', textFile)
      formData.append('textXs', realTextX.toString())
      formData.append('textYs', realTextY.toString())
      formData.append('textWs', realTextW.toString())
      formData.append('textHs', realTextH.toString())
      formData.append('textRotations', textItem.rotation.toString())
    }
  }
  
  return new Promise(async (resolve, reject) => {
    try {
      const response = await fetch('/api/process', {
        method: 'POST',
        body: formData
      })
      
      if (!response.ok) throw new Error('Export failed')
      
      const data = await response.json()
      const jobId = data.job_id
      
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
      const wsUrl = `${protocol}//${window.location.host}/ws/progress/${jobId}`
      const ws = new WebSocket(wsUrl)
      
      ws.onmessage = (event) => {
        const msg = JSON.parse(event.data)
        if (msg.progress !== undefined) {
          progress.value = msg.progress
        }
        if (msg.log) {
          logs.value.push(msg.log)
          
          if (msg.log.includes('%|') || msg.log.includes('Downloading')) {
            const match = msg.log.match(/(\d+%)/)
            if (match) latestLogText.value = `Downloading Model... ${match[1]}`
            else latestLogText.value = 'Downloading Model...'
          } else if (msg.log.includes('frame=')) {
            const fpsMatch = msg.log.match(/fps=\s*(\d+)/)
            latestLogText.value = fpsMatch ? `Rendering at ${fpsMatch[1]}fps...` : 'Rendering...'
          } else if (msg.status === 'transcribing') {
            latestLogText.value = 'Transcribing Audio...'
          }

          nextTick(() => {
            const terminal = document.getElementById('log-terminal')
            if (terminal) terminal.scrollTop = terminal.scrollHeight
          })
        }
        if (msg.status === 'completed') {
          ws.close()
          
          const origName = props.videoFile.name.substring(0, props.videoFile.name.lastIndexOf('.')) || props.videoFile.name;
          const ratio = selectedPreset.value.id === 'custom' 
            ? `${customRatioW.value}x${customRatioH.value}`
            : t('cropper.presets.' + selectedPreset.value.id).split(' ')[0].replace(':', 'x');
          const fileExt = outputFormat.value === 'gif' ? 'gif' : 'mp4'
          downloadFilename.value = `reframe_${origName}_${ratio}.${fileExt}`;
          
          downloadUrl.value = `/api/download/${jobId}?filename=${encodeURIComponent(downloadFilename.value)}`
          isExporting.value = false
          progress.value = 100
          resolve()
        }
        if (msg.status === 'error') {
          ws.close()
          isExporting.value = false
          alert(t('cropper.error_occurred') + msg.detail)
          reject(new Error(msg.detail))
        }
      }
    } catch (error) {
      console.error(error)
      isExporting.value = false
      alert(t('cropper.error_export'))
      reject(error)
    }
  })
}

const getSettings = () => ({
  selectedPreset: selectedPreset.value,
  customRatioW: customRatioW.value,
  customRatioH: customRatioH.value,
  quality: quality.value,
  muteAudio: muteAudio.value,
  useGpu: useGpu.value,
  outputFormat: outputFormat.value,
  trimStart: trimStart.value,
  trimEnd: trimEnd.value,
  
  subtitleEnabled: subtitleEnabled.value,
  subtitleFont: subtitleFont.value,
  subtitleFontSize: subtitleFontSize.value,
  subtitleColor: subtitleColor.value,
  subtitleHighlight: subtitleHighlight.value,
  subtitleStroke: subtitleStroke.value,
  subtitleRelativeX: boxWidth.value > 0 ? subtitleX.value / boxWidth.value : 0,
  subtitleRelativeY: boxHeight.value > 0 ? subtitleY.value / boxHeight.value : 0,
  subtitleRelativeW: boxWidth.value > 0 ? subtitleW.value / boxWidth.value : 0,
  subtitleRelativeH: boxHeight.value > 0 ? subtitleH.value / boxHeight.value : 0,
  
  watermarks: watermarks.value.map(w => ({
    file: w.file,
    rotation: w.rotation,
    opacity: w.opacity,
    relativeX: boxWidth.value > 0 ? w.x / boxWidth.value : 0,
    relativeY: boxHeight.value > 0 ? w.y / boxHeight.value : 0,
    relativeScale: boxWidth.value > 0 ? w.scale / boxWidth.value : 0
  })),
  
  texts: texts.value.map(t => ({
      content: t.content,
      font: t.font,
      color: t.color,
      shadow: t.shadow,
      rotation: t.rotation,
      opacity: t.opacity,
      relativeX: boxWidth.value > 0 ? t.x / boxWidth.value : 0,
      relativeY: boxHeight.value > 0 ? t.y / boxHeight.value : 0,
      relativeScale: boxWidth.value > 0 ? t.scale / boxWidth.value : 0,
    }))
})

const applySettings = (settings: any, options?: any) => {
  const opt = options || { crop: true, trim: true, overlays: true, export: true }

  if (opt.crop) {
    if (settings.selectedPreset !== undefined) selectedPreset.value = settings.selectedPreset
    if (settings.customRatioW !== undefined) customRatioW.value = settings.customRatioW
    if (settings.customRatioH !== undefined) customRatioH.value = settings.customRatioH
  }
  
  if (opt.export) {
    if (settings.quality !== undefined) quality.value = settings.quality
    if (settings.muteAudio !== undefined) muteAudio.value = settings.muteAudio
    if (settings.useGpu !== undefined) useGpu.value = settings.useGpu
    if (settings.outputFormat !== undefined) outputFormat.value = settings.outputFormat
  }
  
  if (opt.trim) {
    if (settings.trimStart !== undefined && settings.trimStart < videoDuration.value) {
      trimStart.value = settings.trimStart
    }
    if (settings.trimEnd !== undefined && settings.trimEnd <= videoDuration.value) {
      trimEnd.value = settings.trimEnd
    }
    if (trimStart.value > trimEnd.value) {
      trimStart.value = Math.max(0, trimEnd.value - 0.1)
    }
  }

  if (opt.crop || opt.trim) {
    initializeCropBox()
  }

  if (opt.overlays) {
    if (settings.subtitleEnabled !== undefined) subtitleEnabled.value = settings.subtitleEnabled
    if (settings.subtitleFont !== undefined) subtitleFont.value = settings.subtitleFont
    if (settings.subtitleFontSize !== undefined) subtitleFontSize.value = settings.subtitleFontSize
    if (settings.subtitleColor !== undefined) subtitleColor.value = settings.subtitleColor
    if (settings.subtitleHighlight !== undefined) subtitleHighlight.value = settings.subtitleHighlight
    if (settings.subtitleStroke !== undefined) subtitleStroke.value = settings.subtitleStroke
    if (settings.subtitleRelativeX !== undefined && boxWidth.value > 0) subtitleX.value = settings.subtitleRelativeX * boxWidth.value
    if (settings.subtitleRelativeY !== undefined && boxHeight.value > 0) subtitleY.value = settings.subtitleRelativeY * boxHeight.value
    if (settings.subtitleRelativeW !== undefined && boxWidth.value > 0) subtitleW.value = settings.subtitleRelativeW * boxWidth.value
    if (settings.subtitleRelativeH !== undefined && boxHeight.value > 0) subtitleH.value = settings.subtitleRelativeH * boxHeight.value

    watermarks.value.forEach(w => URL.revokeObjectURL(w.url))
    watermarks.value = (settings.watermarks || []).map((w: any) => ({
      file: w.file,
      url: URL.createObjectURL(w.file),
      rotation: w.rotation,
      opacity: w.opacity,
      x: boxWidth.value > 0 && w.relativeX !== undefined ? w.relativeX * boxWidth.value : 10,
      y: boxHeight.value > 0 && w.relativeY !== undefined ? w.relativeY * boxHeight.value : 10,
      scale: boxWidth.value > 0 && w.relativeScale !== undefined ? w.relativeScale * boxWidth.value : 1
    }))
    
    texts.value = (settings.texts || []).map((t: any) => ({
        content: t.content,
        font: t.font,
        color: t.color,
        shadow: t.shadow !== undefined ? t.shadow : true,
        rotation: t.rotation,
        opacity: t.opacity !== undefined ? t.opacity : 100,
        x: boxWidth.value > 0 && t.relativeX !== undefined ? t.relativeX * boxWidth.value : 50,
        y: boxHeight.value > 0 && t.relativeY !== undefined ? t.relativeY * boxHeight.value : 50,
        scale: boxWidth.value > 0 && t.relativeScale !== undefined ? t.relativeScale * boxWidth.value : 1
      }))
  }
}

// Resize observer to handle layout shifts robustly
let resizeObserver: ResizeObserver | null = null

watch(subtitleOverlayRef, (el) => {
  if (subtitleObserver) {
    subtitleObserver.disconnect()
    if (el) subtitleObserver.observe(el)
  }
})

onMounted(() => {
  fetchFonts()
  window.addEventListener('fonts-updated', fetchFonts)
  
  subtitleObserver = new ResizeObserver((entries) => {
    for (const entry of entries) {
      if (entry.target === subtitleOverlayRef.value) {
        subtitleW.value = (entry.target as HTMLElement).offsetWidth
        subtitleH.value = (entry.target as HTMLElement).offsetHeight
      }
    }
  })

  if (containerRef.value) {
    resizeObserver = new ResizeObserver(() => {
      initializeCropBox()
    })
    resizeObserver.observe(containerRef.value)
  }
})

onUnmounted(() => {
  window.removeEventListener('fonts-updated', fetchFonts)
  if (resizeObserver) {
    resizeObserver.disconnect()
  }
  if (subtitleObserver) {
    subtitleObserver.disconnect()
  }
})

const getSerializableSettings = async () => {
  const settings = getSettings()
  
  const serializedWatermarks = await Promise.all(settings.watermarks.map(async (w: any) => {
    const base64 = await fileToBase64(w.file)
    return {
      ...w,
      fileBase64: base64,
      fileMime: w.file.type,
      fileName: w.file.name,
      file: undefined // Remove file object
    }
  }))

  return {
    ...settings,
    watermarks: serializedWatermarks
  }
}

const applySerializableSettings = async (config: any) => {
  let restoredWatermarks = []
  if (config.watermarks) {
    restoredWatermarks = await Promise.all(config.watermarks.map(async (w: any) => {
      if (w.fileBase64) {
        const file = await base64ToFile(w.fileBase64, w.fileName || 'watermark.png')
        return {
          ...w,
          file
        }
      }
      return w
    }))
  }

  const restoredSettings = {
    ...config,
    watermarks: restoredWatermarks
  }

  applySettings(restoredSettings)
}

// Config Export/Import logic (Single Mode)
const showExportModal = ref(false)
const exportFilename = ref('my_settings')
const importConfigInput = ref<HTMLInputElement | null>(null)

const handleExportConfig = async () => {
  try {
    const config = await getSerializableSettings()
    const json = JSON.stringify(config)
    const blob = new Blob([json], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${exportFilename.value}.reframe_config`
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
    showExportModal.value = false
  } catch (error) {
    console.error('Error exporting config:', error)
  }
}

const triggerImportConfig = () => {
  importConfigInput.value?.click()
}

const importConfig = async (e: Event) => {
  const target = e.target as HTMLInputElement
  if (!target.files || target.files.length === 0) return
  const file = target.files[0]
  try {
    const text = await file.text()
    const config = JSON.parse(text)
    await applySerializableSettings(config)
  } catch (error) {
    console.error('Error importing config:', error)
    alert(t('config.error_import'))
  }
  target.value = ''
}

defineExpose({
  runExport: exportVideo,
  getSettings,
  applySettings,
  getSerializableSettings,
  applySerializableSettings
})
</script>

<template>
  <div class="cropper-layout">
    <div class="video-section glass">
      <div class="video-container" ref="containerRef">
        <video 
          ref="videoRef" 
          :src="videoUrl" 
          controls 
          class="video-element"
          @loadedmetadata="onVideoLoaded"
        ></video>
        
        <!-- Crop Overlay Box -->
        <div 
          v-if="boxWidth > 0"
          class="crop-box"
          :style="{
            left: `${boxLeft}px`,
            top: `${boxTop}px`,
            width: `${boxWidth}px`,
            height: `${boxHeight}px`
          }"
          @mousedown="startDrag"
        >
          <!-- Logos inside crop box -->
          <div 
            v-for="(wm, i) in watermarks"
            :key="'wm-'+i"
            :id="`logo-overlay-${i}`"
            class="logo-overlay"
            :style="{
              left: `${wm.x}px`,
              top: `${wm.y}px`,
              width: `${150 * wm.scale}px`,
              transform: `rotate(${wm.rotation}deg)`,
              opacity: wm.opacity / 100
            }"
            @mousedown="startLogoDrag($event, i)"
          >
            <img :src="wm.url" :id="`logo-img-${i}`" alt="Watermark Logo" class="logo-img" />
            <div class="rotate-handle" @mousedown.stop="startRotate($event, i)">↻</div>
          </div>
          
          <!-- Text Overlays -->
          <div 
            v-for="(tItem, i) in texts"
            :key="'text-'+i"
            :id="`text-overlay-${i}`"
            class="text-overlay"
            :style="{
              left: `${tItem.x}px`,
              top: `${tItem.y}px`,
              transform: `rotate(${tItem.rotation}deg)`,
              color: tItem.color,
              fontFamily: tItem.font,
              textShadow: tItem.shadow ? '2px 2px 4px rgba(0,0,0,0.8)' : 'none',
              fontSize: `${24 * tItem.scale}px`,
              opacity: tItem.opacity !== undefined ? tItem.opacity / 100 : 1
            }"
            @mousedown="startTextDrag($event, i)"
            v-show="tItem.content"
          >
            <div class="text-content" style="white-space: pre-wrap; text-align: center; font-weight: bold; line-height: 1.2;">{{ tItem.content }}</div>
            <div class="rotate-handle" @mousedown.stop="startTextRotate($event, i)">↻</div>
          </div>
          
          <!-- Subtitle Overlay -->
          <div
            v-if="subtitleEnabled"
            class="subtitle-overlay"
            ref="subtitleOverlayRef"
            :style="{
              left: `${subtitleX}px`,
              top: `${subtitleY}px`,
              width: 'max-content',
              height: 'max-content'
            }"
            @mousedown="startSubtitleDrag($event)"
          >
            <div class="subtitle-preview" :style="{ 
              fontFamily: subtitleFont, 
              color: subtitleColor, 
              fontSize: `${subtitleFontSize}px`,
              WebkitTextStroke: `${Math.max(1, subtitleFontSize * 0.1)}px ${subtitleStroke}`,
              textShadow: `${Math.max(1, subtitleFontSize * 0.05)}px ${Math.max(1, subtitleFontSize * 0.05)}px 0 rgba(0,0,0,0.8)`
            }">
              <span class="preview-text">
                Tiktok <span :style="{ color: subtitleHighlight }">Subtitle</span>
              </span>
            </div>
          </div>
        </div>
      </div>
    </div>
    
    <div class="controls-section glass">
      <h3>{{ $t('cropper.format_title') }}</h3>
      <div class="preset-buttons">
        <button 
          v-for="preset in presets" 
          :key="preset.id"
          class="btn preset-btn"
          :class="{ active: selectedPreset.id === preset.id }"
          @click="selectPreset(preset)"
          :disabled="isExporting"
        >
          {{ $t('cropper.presets.' + preset.id) }}
        </button>
      </div>
      
      <div v-if="selectedPreset.id === 'custom'" class="custom-ratio-inputs">
        <label>
          {{ $t('cropper.width') }}
          <input type="number" min="1" v-model.number="customRatioW" class="input-number" />
        </label>
        <label>
          {{ $t('cropper.height') }}
          <input type="number" min="1" v-model.number="customRatioH" class="input-number" />
        </label>
      </div>

      <div class="advanced-toggle" style="margin-top: 0.5rem; margin-bottom: 0.5rem;">
        <label class="option-label checkbox-label" style="cursor: pointer; color: var(--accent-neon); font-weight: 600;">
          <input type="checkbox" v-model="showAdvancedOptions" />
          <span>{{ $t('cropper.advanced_options') }}</span>
        </label>
      </div>

      <template v-if="showAdvancedOptions">
        <div v-if="videoDuration > 0" class="options-section">
          <div class="option-label">
            <span>{{ $t('cropper.trim_video') }}</span>
            <div class="trim-inputs">
              <input type="number" step="0.1" min="0" :max="trimEnd" v-model.number="trimStart" class="input-number" />
              <span>{{ $t('cropper.to') }}</span>
              <input type="number" step="0.1" :min="trimStart" :max="videoDuration" v-model.number="trimEnd" class="input-number" />
              <span>{{ $t('cropper.sec') }}</span>
            </div>
            <div class="range-slider-container">
              <div class="slider-track-bg"></div>
              <div class="slider-track-fill" :style="{
                left: `${(trimStart / videoDuration) * 100}%`,
                width: `${((trimEnd - trimStart) / videoDuration) * 100}%`
              }"></div>
              <input type="range" min="0" :max="videoDuration" step="0.1" v-model.number="trimStart" class="range-slider" />
              <input type="range" min="0" :max="videoDuration" step="0.1" v-model.number="trimEnd" class="range-slider" />
            </div>
          </div>
        </div>
        
        <div class="options-section">
          <div class="option-label">
            <span>{{ $t('cropper.watermark') }}</span>
            <div class="logo-upload-area">
              <input type="file" multiple accept="image/png, image/jpeg, image/svg+xml" @change="handleLogoUpload" class="file-input" />
              <div v-for="(wm, index) in watermarks" :key="'wm-ctrl-'+index" class="logo-controls" style="margin-top: 0.5rem;">
                <img :src="wm.url" style="width: 30px; height: 30px; object-fit: contain;" />
                <label>
                  {{ $t('cropper.size') }}
                  <input type="range" min="0.2" max="3" step="0.1" v-model.number="wm.scale" />
                </label>
                <label>
                  {{ $t('cropper.opacity') }}
                  <input type="range" min="0" max="100" step="1" v-model.number="wm.opacity" />
                </label>
                <button class="btn btn-secondary btn-sm" @click="removeWatermark(index)">{{ $t('cropper.remove') }}</button>
              </div>
            </div>
          </div>
        </div>
        
        <div class="options-section">
          <div class="option-label">
            <div style="display: flex; justify-content: space-between; align-items: center; width: 100%;">
              <span>{{ $t('cropper.text_overlay') }}</span>
              <button class="btn btn-sm btn-secondary" @click="addText">{{ $t('cropper.text_add') }}</button>
            </div>
            
            <div v-for="(tItem, index) in texts" :key="'text-ctrl-'+index" class="text-controls" style="display: flex; flex-direction: column; gap: 0.5rem; width: 100%; margin-top: 1rem; padding: 0.5rem; background: var(--bg-tertiary); border-radius: 8px;">
              <div style="display: flex; justify-content: space-between;">
                <strong>Text {{ index + 1 }}</strong>
                <button class="btn btn-sm btn-secondary" @click="removeText(index)">{{ $t('cropper.text_remove') }}</button>
              </div>
              <textarea v-model="tItem.content" :placeholder="$t('cropper.text_content')" class="input-text" rows="2"></textarea>
              <div style="display: flex; gap: 0.5rem;">
                <select v-model="tItem.font" class="input-select" style="flex: 1;">
                  <option v-for="font in fonts" :key="font" :value="font">{{ font }}</option>
                </select>
                <input type="color" v-model="tItem.color" style="height: 38px; width: 50px; border: 1px solid var(--glass-border); border-radius: 4px; background: transparent; cursor: pointer;" />
              </div>
              <div style="display: flex; gap: 1rem; align-items: center;">
                <label style="flex: 1;">
                  {{ $t('cropper.size') }}
                  <input type="range" min="0.5" max="5" step="0.1" v-model.number="tItem.scale" style="width: 100%;" />
                </label>
                <label class="checkbox-label" style="margin-top: 18px;">
                  <input type="checkbox" v-model="tItem.shadow" />
                  <span>{{ $t('cropper.text_shadow') }}</span>
                </label>
                <label style="flex: 1;">
                  {{ $t('cropper.opacity') }}
                  <input type="range" min="0" max="100" step="1" v-model.number="tItem.opacity" style="width: 100%;" />
                </label>
              </div>
            </div>
          </div>
        </div>
        
        <div class="options-section">
          <div class="option-label">
            <div style="display: flex; justify-content: space-between; align-items: center; width: 100%;">
              <span>{{ $t('cropper.subtitles', 'Auto Subtitles') }}</span>
              <label class="checkbox-label">
                <input type="checkbox" v-model="subtitleEnabled">
                <span>{{ $t('cropper.enable', 'Enable') }}</span>
              </label>
            </div>
            
            <div v-if="subtitleEnabled" class="text-controls" style="display: flex; flex-direction: column; gap: 0.5rem; width: 100%; margin-top: 1rem; padding: 0.5rem; background: var(--bg-tertiary); border-radius: 8px;">
              <div style="display: flex; gap: 0.5rem;">
                <select v-model="subtitleFont" class="input-select" style="flex: 1;">
                  <option v-for="font in fonts" :key="font" :value="font">{{ font }}</option>
                </select>
              </div>
              <div style="display: flex; gap: 0.5rem; align-items: center; margin-top: 0.5rem;">
                <label style="flex: 1; display: flex; flex-direction: column; gap: 0.25rem;">
                  <span style="font-size: 0.8rem; color: var(--text-secondary);">{{ $t('cropper.font_size', 'Font Size') }}</span>
                  <input type="range" v-model.number="subtitleFontSize" min="10" max="150" />
                </label>
              </div>
              <div style="display: flex; gap: 1rem; align-items: center;">
                <label style="flex: 1; display: flex; flex-direction: column; gap: 0.25rem;">
                  <span style="font-size: 0.8rem">{{ $t('cropper.subtitle_base', 'Base') }}</span>
                  <input type="color" v-model="subtitleColor" style="height: 38px; width: 100%; border: 1px solid var(--glass-border); border-radius: 4px; background: transparent; cursor: pointer;" />
                </label>
                <label style="flex: 1; display: flex; flex-direction: column; gap: 0.25rem;">
                  <span style="font-size: 0.8rem">{{ $t('cropper.subtitle_highlight', 'Highlight') }}</span>
                  <input type="color" v-model="subtitleHighlight" style="height: 38px; width: 100%; border: 1px solid var(--glass-border); border-radius: 4px; background: transparent; cursor: pointer;" />
                </label>
                <label style="flex: 1; display: flex; flex-direction: column; gap: 0.25rem;">
                  <span style="font-size: 0.8rem">{{ $t('cropper.subtitle_stroke', 'Stroke') }}</span>
                  <input type="color" v-model="subtitleStroke" style="height: 38px; width: 100%; border: 1px solid var(--glass-border); border-radius: 4px; background: transparent; cursor: pointer;" />
                </label>
              </div>
            </div>
          </div>
        </div>
      </template>

      <div class="options-section">
        <label class="option-label">
          <span>{{ $t('cropper.output_format') }}</span>
          <select v-model="outputFormat" class="input-select" :disabled="isExporting">
            <option value="mp4">{{ $t('cropper.output_format_mp4') }}</option>
            <option value="gif">{{ $t('cropper.output_format_gif') }}</option>
          </select>
        </label>

        <label class="option-label">
          <span>{{ $t('cropper.quality') }}</span>
          <select v-model="quality" class="input-select" :disabled="isExporting">
            <option value="high">{{ $t('cropper.quality_high') }}</option>
            <option value="medium">{{ $t('cropper.quality_medium') }}</option>
          </select>
        </label>
        
        <label class="option-label checkbox-label">
          <input type="checkbox" v-model="muteAudio" :disabled="isExporting || outputFormat === 'gif'" />
          <span>{{ $t('cropper.mute_audio') }}</span>
        </label>
        
        <label class="option-label checkbox-label">
          <input type="checkbox" v-model="useGpu" :disabled="isExporting || outputFormat === 'gif'" />
          <span>{{ $t('cropper.hardware_acceleration') }}</span>
        </label>
      </div>
      
      <div class="export-area" v-if="!isBatchMode || downloadUrl">
        <button v-if="!isBatchMode" class="btn btn-export" @click="exportVideo" :disabled="isExporting">
          {{ isExporting ? latestLogText : (downloadUrl ? $t('cropper.render_again') : $t('cropper.crop_export')) }}
        </button>
        <button v-if="isExporting" class="btn btn-secondary" @click="showLogs = !showLogs">
          {{ showLogs ? 'Hide Console' : 'Show Console' }}
        </button>
        <a v-if="downloadUrl && !isExporting" :href="downloadUrl" :download="downloadFilename" class="btn btn-success">
          {{ $t('cropper.download') }}
        </a>
        <button v-if="!isBatchMode" class="btn btn-secondary" @click="emit('reset')" :disabled="isExporting">
          {{ $t('cropper.choose_new') }}
        </button>
      </div>
      
      <div v-if="isExporting || progress > 0" class="progress-bar-container">
        <div class="progress-bar" :style="{ width: `${progress}%` }"></div>
        <div class="progress-text">{{ progress }}%</div>
      </div>
      
      <div class="config-actions" v-if="!isBatchMode">
        <button class="btn btn-secondary btn-sm" @click="showExportModal = true" :disabled="isExporting">{{ $t('config.export') }}</button>
        <button class="btn btn-secondary btn-sm" @click="triggerImportConfig" :disabled="isExporting">{{ $t('config.import') }}</button>
        <input type="file" ref="importConfigInput" style="display: none" accept=".reframe_config" @change="importConfig" />
      </div>
    </div>

    <!-- Export Config Modal -->
    <div v-if="showExportModal" class="modal-backdrop">
      <div class="modal-content glass">
        <h3>{{ $t('config.export_title') }}</h3>
        <label class="option-label" style="margin-top: 1rem;">
          <span>{{ $t('config.filename') }}</span>
          <div style="display: flex; gap: 0.5rem; align-items: center;">
            <input type="text" v-model="exportFilename" class="input-text" />
            <span>.reframe_config</span>
          </div>
        </label>
        <div style="display: flex; gap: 1rem; margin-top: 1.5rem; justify-content: flex-end;">
          <button class="btn btn-secondary" @click="showExportModal = false">{{ $t('config.cancel') }}</button>
          <button class="btn btn-success" @click="handleExportConfig">{{ $t('config.save') }}</button>
        </div>
      </div>
    </div>
    
    <!-- Floating Terminal Modal -->
    <div v-if="showLogs && isExporting" class="terminal-modal glass">
      <div class="terminal-header">
        <span>Render Console</span>
        <button class="close-btn" @click="showLogs = false">&times;</button>
      </div>
      <div class="terminal-body" id="log-terminal">
        <div v-for="(log, idx) in logs" :key="idx" class="log-line">{{ log }}</div>
        <div v-if="logs.length === 0" class="log-line text-muted">Waiting for logs...</div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.terminal-modal {
  position: fixed;
  bottom: 20px;
  right: 20px;
  width: 450px;
  height: 300px;
  background: rgba(10, 15, 30, 0.95);
  border: 1px solid var(--accent-neon);
  border-radius: 8px;
  box-shadow: 0 10px 30px rgba(0, 0, 0, 0.5), 0 0 15px rgba(59, 130, 246, 0.2);
  display: flex;
  flex-direction: column;
  z-index: 1000;
  overflow: hidden;
  backdrop-filter: blur(10px);
}

.terminal-header {
  background: rgba(255, 255, 255, 0.1);
  padding: 8px 12px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-family: monospace;
  font-size: 0.9rem;
  color: var(--text-primary);
  border-bottom: 1px solid rgba(255, 255, 255, 0.1);
}

.close-btn {
  background: transparent;
  border: none;
  color: #ff5f56;
  font-size: 1.5rem;
  line-height: 1;
  cursor: pointer;
}

.terminal-body {
  flex: 1;
  overflow-y: auto;
  padding: 10px;
  font-family: 'Fira Code', monospace;
  font-size: 0.8rem;
  color: #00ff00;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.log-line {
  white-space: pre-wrap;
  word-break: break-all;
}

.text-muted {
  color: #888;
}

.cropper-container {
  display: flex;
  gap: 2rem;
  width: 100%;
  max-width: 1400px;
}

.cropper-layout {
  display: flex;
  gap: 2rem;
  width: 100%;
  max-width: 1400px;
}

@media (max-width: 768px) {
  .cropper-layout {
    flex-direction: column;
  }
}

.video-section {
  flex: 2.5;
  padding: 1rem;
  display: flex;
  justify-content: center;
  align-items: center;
  background: var(--bg-secondary);
  height: 75vh;
  min-height: 600px;
}

.video-container {
  position: relative;
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
}

.video-element {
  width: 100%;
  height: 100%;
  object-fit: contain;
}

.crop-box {
  position: absolute;
  border: 2px solid var(--accent-neon);
  box-shadow: 0 0 0 9999px rgba(0, 0, 0, 0.6);
  cursor: grab;
  z-index: 10;
  transition: width 0.2s, height 0.2s; /* smooth resize when changing preset */
}

.crop-box:active {
  cursor: grabbing;
}

/* Corner markers removed as requested */

.controls-section {
  flex: 1;
  padding: 2rem;
  display: flex;
  flex-direction: column;
  gap: 1.5rem;
}

.preset-buttons {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.preset-btn {
  background: var(--bg-tertiary);
  color: var(--text-primary);
  border: 1px solid var(--glass-border);
}

.preset-btn.active {
  background: var(--accent);
  border-color: var(--accent-neon);
  box-shadow: 0 0 15px rgba(59, 130, 246, 0.3);
}

.export-area {
  display: flex;
  flex-direction: column;
  gap: 1rem;
  margin-top: auto;
}

.btn-export {
  padding: 1rem;
  font-size: 1.1rem;
}

.btn-secondary {
  background: transparent;
  border: 1px solid var(--glass-border);
}
.btn-secondary:hover:not(:disabled) {
  background: var(--bg-tertiary);
}

.btn-sm {
  padding: 0.25rem 0.75rem;
  font-size: 0.85rem;
}

.logo-overlay {
  position: absolute;
  cursor: move;
  transform-origin: center center;
}

.logo-overlay:active {
  cursor: grabbing;
}

.logo-img {
  width: 100%;
  height: auto;
  display: block;
  pointer-events: none;
}

.text-overlay {
  position: absolute;
  cursor: move;
  transform-origin: center center;
  padding: 10px;
  border: 1px dashed transparent;
  width: max-content;
  white-space: pre-wrap;
}

.text-overlay:hover {
  border-color: rgba(255,255,255,0.5);
}

.text-overlay:active {
  cursor: grabbing;
}

.subtitle-overlay {
  position: absolute;
  border: 2px dashed var(--accent-neon);
  background: rgba(255, 255, 255, 0.1);
  cursor: grab;
  z-index: 25;
  display: flex;
  align-items: center;
  justify-content: center;
}

.subtitle-overlay:hover {
  background: rgba(255, 255, 255, 0.2);
}

.subtitle-overlay:active {
  cursor: grabbing;
}

.subtitle-preview {
  font-weight: bold;
  text-align: center;
  line-height: 1.2;
  user-select: none;
  pointer-events: none;
  width: 100%;
  paint-order: stroke fill;
}

.rotate-handle {
  position: absolute;
  top: -25px;
  left: 50%;
  transform: translateX(-50%);
  width: 24px;
  height: 24px;
  background: var(--accent);
  color: white;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: crosshair;
  font-size: 14px;
  user-select: none;
  box-shadow: 0 2px 4px rgba(0,0,0,0.3);
}

.rotate-handle:active {
  background: var(--accent-hover);
}

.logo-upload-area {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.file-input {
  font-size: 0.9rem;
  color: var(--text-secondary);
}

.input-text {
  width: 100%;
  background: var(--bg-tertiary);
  border: 1px solid var(--glass-border);
  color: var(--text-primary);
  padding: 0.5rem;
  border-radius: 4px;
  resize: vertical;
  font-family: inherit;
}

.logo-controls {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  background: rgba(0,0,0,0.2);
  padding: 0.5rem;
  border-radius: var(--radius-sm);
}

.logo-controls label {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.9rem;
  flex: 1 1 auto;
}

.options-section {
  display: flex;
  flex-direction: column;
  gap: 1rem;
  margin-bottom: 1rem;
}

.trim-inputs {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.9rem;
}

.range-slider-container {
  position: relative;
  height: 24px;
  width: 100%;
  margin-top: 0.5rem;
}

.range-slider {
  position: absolute;
  width: 100%;
  pointer-events: none;
  -webkit-appearance: none;
  background: transparent;
  margin: 0;
  top: 50%;
  transform: translateY(-50%);
}

.range-slider::-webkit-slider-thumb {
  pointer-events: auto;
  -webkit-appearance: none;
  height: 18px;
  width: 18px;
  border-radius: 50%;
  background: var(--accent);
  border: 2px solid white;
  cursor: grab;
  margin-top: -7px;
  box-shadow: 0 1px 3px rgba(0,0,0,0.4);
}

.range-slider::-webkit-slider-thumb:active {
  cursor: grabbing;
}

.range-slider::-moz-range-thumb {
  pointer-events: auto;
  height: 18px;
  width: 18px;
  border-radius: 50%;
  background: var(--accent);
  border: 2px solid white;
  cursor: grab;
  box-shadow: 0 1px 3px rgba(0,0,0,0.4);
}

.slider-track-bg {
  position: absolute;
  top: 50%;
  transform: translateY(-50%);
  width: 100%;
  height: 4px;
  background: var(--bg-tertiary);
  border-radius: 2px;
}

.slider-track-fill {
  position: absolute;
  top: 50%;
  transform: translateY(-50%);
  height: 4px;
  background: var(--accent);
  border-radius: 2px;
  transition: width 0.1s, left 0.1s;
}

.range-slider::-webkit-slider-runnable-track {
  background: transparent;
}

.option-label {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  font-size: 0.9rem;
}

.checkbox-label {
  flex-direction: row;
  align-items: center;
  gap: 0.75rem;
  cursor: pointer;
}

.input-select {
  background: var(--bg-tertiary);
  color: var(--text-primary);
  border: 1px solid var(--glass-border);
  padding: 0.75rem;
  border-radius: var(--radius-md);
  outline: none;
}
.input-select:focus {
  border-color: var(--accent);
}

.custom-ratio-inputs {
  display: flex;
  gap: 1rem;
  margin-top: 0.5rem;
}

.custom-ratio-inputs label {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.9rem;
}

.input-number {
  background: var(--bg-tertiary);
  color: var(--text-primary);
  border: 1px solid var(--glass-border);
  padding: 0.5rem;
  border-radius: var(--radius-md);
  width: 70px;
  outline: none;
}
.input-number:focus {
  border-color: var(--accent);
}

.btn-success {
  background: var(--success);
  text-decoration: none;
  text-align: center;
  padding: 1rem;
  font-size: 1.1rem;
}
.btn-success:hover {
  background: #059669; /* hover success */
}

.progress-bar-container {
  position: relative;
  height: 24px;
  background: var(--bg-tertiary);
  border-radius: var(--radius-full);
  overflow: hidden;
  margin-top: 0.5rem;
  box-shadow: inset 0 2px 4px rgba(0,0,0,0.2);
}

.progress-bar {
  height: 100%;
  background: linear-gradient(90deg, var(--accent-hover), var(--accent-neon));
  transition: width 0.3s ease;
}

.progress-text {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 0.85rem;
  font-weight: bold;
  text-shadow: 0 1px 2px rgba(0,0,0,0.5);
}

.config-actions {
  display: flex;
  gap: 1rem;
  margin-top: 1rem;
  justify-content: center;
}

.modal-backdrop {
  position: fixed;
  top: 0;
  left: 0;
  width: 100vw;
  height: 100vh;
  background: rgba(0, 0, 0, 0.7);
  backdrop-filter: blur(5px);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
}

.modal-content {
  padding: 2rem;
  border-radius: var(--radius-lg);
  min-width: 350px;
  box-shadow: 0 15px 35px rgba(0, 0, 0, 0.5);
}
</style>
