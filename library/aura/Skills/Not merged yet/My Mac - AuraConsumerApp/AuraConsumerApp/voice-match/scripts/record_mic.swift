//
//  record_mic.swift
//  Records an audio input device to a WAV for a fixed number of seconds.
//
//  Used to capture Aurie's voice from the simulator. `simctl io recordVideo` carries no audio
//  track, so the capture has to come from an input device: preferably a loopback driver
//  (BlackHole) that the Simulator's audio output is routed to, which records Aurie and nothing
//  else; otherwise the Mac microphone, which also picks up the room.
//
//  Usage: swift record_mic.swift <out.wav> <seconds> [input device name, e.g. "BlackHole 2ch"]
//

import AVFoundation
import CoreAudio
import Foundation

func fail(_ message: String) -> Never {
    FileHandle.standardError.write((message + "\n").data(using: .utf8) ?? Data())
    exit(1)
}

/// The CoreAudio id of the input-capable device whose name matches exactly.
func deviceID(named name: String) -> AudioDeviceID? {
    var address = AudioObjectPropertyAddress(
        mSelector: kAudioHardwarePropertyDevices,
        mScope: kAudioObjectPropertyScopeGlobal,
        mElement: kAudioObjectPropertyElementMain
    )
    var size: UInt32 = 0
    guard AudioObjectGetPropertyDataSize(AudioObjectID(kAudioObjectSystemObject), &address, 0, nil, &size) == noErr
    else { return nil }
    var ids = [AudioDeviceID](repeating: 0, count: Int(size) / MemoryLayout<AudioDeviceID>.size)
    guard AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &address, 0, nil, &size, &ids) == noErr
    else { return nil }
    for id in ids {
        var nameAddress = AudioObjectPropertyAddress(
            mSelector: kAudioObjectPropertyName,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain
        )
        var cfName: Unmanaged<CFString>?
        var nameSize = UInt32(MemoryLayout<Unmanaged<CFString>?>.size)
        if AudioObjectGetPropertyData(id, &nameAddress, 0, nil, &nameSize, &cfName) == noErr,
           let value = cfName?.takeRetainedValue() as String?, value == name {
            return id
        }
    }
    return nil
}

let args = CommandLine.arguments
guard args.count >= 3, let seconds = Double(args[2]) else {
    fail("usage: swift record_mic.swift <out.wav> <seconds> [input device name]")
}

let engine = AVAudioEngine()
let input = engine.inputNode

if args.count >= 4 {
    guard var device = deviceID(named: args[3]) else { fail("no audio device named \"\(args[3])\"") }
    guard let unit = input.audioUnit else { fail("input node has no audio unit") }
    let status = AudioUnitSetProperty(
        unit, kAudioOutputUnitProperty_CurrentDevice, kAudioUnitScope_Global, 0,
        &device, UInt32(MemoryLayout<AudioDeviceID>.size)
    )
    guard status == noErr else { fail("could not select \"\(args[3])\" (status \(status))") }
}

// Recorded at the device's native format; voice_match converts to 16 kHz mono when it reads.
let format = input.inputFormat(forBus: 0)
var file: AVAudioFile?
do {
    file = try AVAudioFile(forWriting: URL(fileURLWithPath: args[1]), settings: format.settings)
} catch {
    fail("cannot open output: \(error)")
}
var frames: AVAudioFramePosition = 0
input.installTap(onBus: 0, bufferSize: 4096, format: format) { buffer, _ in
    frames += AVAudioFramePosition(buffer.frameLength)
    do {
        try file?.write(from: buffer)
    } catch {
        FileHandle.standardError.write("write failed: \(error)\n".data(using: .utf8) ?? Data())
    }
}
do {
    try engine.start()
} catch {
    fail("recording did not start — check microphone permission: \(error)")
}
RunLoop.main.run(until: Date().addingTimeInterval(seconds))
engine.stop()
input.removeTap(onBus: 0)
// AVAudioFile finalises its header only on deinit; top-level globals are never released.
file = nil
print("saved \(args[1]) (\(Int(format.sampleRate)) Hz, \(format.channelCount) ch, \(frames) frames)")
