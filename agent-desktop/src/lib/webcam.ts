import type { AgentBridge } from "@/lib/bridge"

type CameraPayload = Record<string, unknown>

function cameraError(error: unknown) {
  if (error instanceof DOMException) {
    if (error.name === "NotAllowedError") return "Windows camera permission was denied by the local user."
    if (error.name === "NotFoundError") return "No camera was detected on this Windows device."
    if (error.name === "NotReadableError") return "The camera is busy in another application."
    return `${error.name}: ${error.message}`
  }
  return error instanceof Error ? error.message : "Unable to access the local camera."
}

function toBase64(blob: Blob) {
  return blob.arrayBuffer().then((buffer) => {
    const bytes = new Uint8Array(buffer)
    let binary = ""
    for (const byte of bytes) binary += String.fromCharCode(byte)
    return btoa(binary)
  })
}

export class LocalWebcam {
  private stream: MediaStream | null = null
  private video: HTMLVideoElement | null = null
  private canvas = document.createElement("canvas")
  private timer: number | null = null
  private sending = false
  private bridge: AgentBridge

  constructor(bridge: AgentBridge) {
    this.bridge = bridge
  }

  async list() {
    let probe: MediaStream | null = null
    try {
      probe = await navigator.mediaDevices.getUserMedia({ video: true, audio: false })
      const devices = await navigator.mediaDevices.enumerateDevices()
      const cameras = devices
        .filter((device) => device.kind === "videoinput")
        .map((device, index) => ({ index, device_id: device.deviceId, label: device.label || `Camera ${index + 1}` }))
      return { items: cameras, count: cameras.length, available: cameras.length > 0, capture_backend: "webview2", opencv_available: false, cv2_available: false, agent_packaged: true }
    } catch (error) {
      return { items: [], count: 0, available: false, capture_backend: "webview2", opencv_available: false, cv2_available: false, agent_packaged: true, error: cameraError(error) }
    } finally {
      probe?.getTracks().forEach((track) => track.stop())
    }
  }

  async snapshot(payload: CameraPayload) {
    try {
      await this.open(payload)
      const frame = await this.capture(Number(payload.quality ?? 75))
      return { mime: "image/jpeg", image: frame, capture_backend: "webview2" }
    } catch (error) {
      return { error: cameraError(error) }
    } finally {
      this.stop()
    }
  }

  async start(payload: CameraPayload) {
    if (this.stream) return { status: "already_running", capture_backend: "webview2" }
    try {
      await this.open(payload)
      const fps = Math.max(1, Math.min(Number(payload.fps ?? 15), 20))
      const quality = Math.max(25, Math.min(Number(payload.quality ?? 55), 85))
      const interval = Math.round(1000 / fps)
      this.timer = window.setInterval(() => { void this.sendFrame(quality) }, interval)
      return { status: "running", capture_backend: "webview2", fps }
    } catch (error) {
      this.stop()
      return { error: cameraError(error) }
    }
  }

  stop() {
    if (this.timer !== null) window.clearInterval(this.timer)
    this.timer = null
    this.stream?.getTracks().forEach((track) => track.stop())
    this.stream = null
    this.video?.remove()
    this.video = null
    this.sending = false
    return { status: "stopped", capture_backend: "webview2" }
  }

  private async open(payload: CameraPayload) {
    if (!navigator.mediaDevices?.getUserMedia) throw new Error("This Windows WebView does not support local camera capture.")
    const width = Math.max(160, Number(payload.width ?? 640))
    const height = Math.max(120, Number(payload.height ?? 360))
    const deviceId = typeof payload.device_id === "string" ? payload.device_id : undefined
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: false,
      video: { width: { ideal: width }, height: { ideal: height }, ...(deviceId ? { deviceId: { exact: deviceId } } : {}) },
    })
    this.video = document.createElement("video")
    this.video.muted = true
    this.video.playsInline = true
    this.video.style.cssText = "position:fixed;width:1px;height:1px;opacity:0;pointer-events:none;"
    this.video.srcObject = this.stream
    document.body.append(this.video)
    await this.video.play()
    await new Promise<void>((resolve) => window.setTimeout(resolve, 120))
  }

  private async capture(quality: number) {
    if (!this.video || this.video.videoWidth < 1 || this.video.videoHeight < 1) throw new Error("The camera did not produce a video frame.")
    this.canvas.width = this.video.videoWidth
    this.canvas.height = this.video.videoHeight
    const context = this.canvas.getContext("2d")
    if (!context) throw new Error("Unable to create the camera frame buffer.")
    context.drawImage(this.video, 0, 0, this.canvas.width, this.canvas.height)
    const blob = await new Promise<Blob | null>((resolve) => this.canvas.toBlob(resolve, "image/jpeg", quality / 100))
    if (!blob) throw new Error("Unable to encode the camera frame.")
    return toBase64(blob)
  }

  private async sendFrame(quality: number) {
    if (this.sending || !this.stream) return
    this.sending = true
    try {
      const frame = await this.capture(quality)
      await this.bridge.notify("webcam.frame", { frame, mime: "image/jpeg" })
    } catch (error) {
      await this.bridge.notify("webcam.error", { error: cameraError(error) })
      this.stop()
    } finally {
      this.sending = false
    }
  }
}