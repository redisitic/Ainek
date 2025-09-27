// Minimal speech service wrapping Web Speech API for recognition and synthesis

export function isSpeechSupported() {
  const SpeechRecognition =
    window.SpeechRecognition || window.webkitSpeechRecognition || null;
  return !!SpeechRecognition && !!window.speechSynthesis;
}

export function startRecognition({
  lang = "en-US",
  continuous = false,
  interimResults = false,
  onResult,
  onError,
  onEnd,
}) {
  const SpeechRecognition =
    window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRecognition) {
    onError && onError(new Error("Speech recognition not supported"));
    return () => {};
  }

  const recognition = new SpeechRecognition();
  recognition.lang = lang;
  recognition.continuous = continuous;
  recognition.interimResults = interimResults;

  recognition.onresult = (event) => {
    try {
      const resultIndex = event.resultIndex;
      const transcript = event.results[resultIndex][0].transcript;
      const isFinal = event.results[resultIndex].isFinal;
      onResult && onResult({ transcript, isFinal, event });
    } catch (e) {
      onError && onError(e);
    }
  };

  recognition.onerror = (event) => {
    onError && onError(event.error || event);
  };

  recognition.onend = () => {
    onEnd && onEnd();
  };

  recognition.start();

  // Return a stopper
  return () => {
    try {
      recognition.stop();
    } catch {}
  };
}

export function speak(text, { rate = 1, pitch = 1, volume = 1, lang = "en-US" } = {}) {
  if (!window.speechSynthesis) return;
  const utter = new SpeechSynthesisUtterance(text);
  utter.rate = rate;
  utter.pitch = pitch;
  utter.volume = volume;
  utter.lang = lang;
  window.speechSynthesis.cancel();
  window.speechSynthesis.speak(utter);
}

export function stopSpeaking() {
  try {
    window.speechSynthesis && window.speechSynthesis.cancel();
  } catch {}
}