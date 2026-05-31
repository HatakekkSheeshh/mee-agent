/**
 * AudioWorklet processor for resampling audio to 16kHz mono.
 * Adapted from WhisperLive's Chrome extension audiopreprocessor.js.
 */
class AudioProcessor extends AudioWorkletProcessor {
    constructor() {
        super();
        this.bufferSize = 4096;
        this.buffer = new Float32Array(this.bufferSize);
        this.bufferIndex = 0;
        this.targetSampleRate = 16000;
    }

    process(inputs, outputs, parameters) {
        const input = inputs[0];
        if (!input || !input.length) return true;

        const channelData = input[0]; // mono
        for (let i = 0; i < channelData.length; i++) {
            this.buffer[this.bufferIndex++] = channelData[i];

            if (this.bufferIndex >= this.bufferSize) {
                // Resample from source sample rate to 16kHz
                const resampled = this.resample(this.buffer, sampleRate, this.targetSampleRate);
                this.port.postMessage(resampled);
                this.bufferIndex = 0;
            }
        }
        return true;
    }

    resample(audioData, fromRate, toRate) {
        if (fromRate === toRate) {
            return new Float32Array(audioData);
        }
        const ratio = fromRate / toRate;
        const newLength = Math.round(audioData.length / ratio);
        const result = new Float32Array(newLength);
        for (let i = 0; i < newLength; i++) {
            const srcIndex = i * ratio;
            const low = Math.floor(srcIndex);
            const high = Math.min(low + 1, audioData.length - 1);
            const frac = srcIndex - low;
            result[i] = audioData[low] * (1 - frac) + audioData[high] * frac;
        }
        return result;
    }
}

registerProcessor("audio-processor", AudioProcessor);
