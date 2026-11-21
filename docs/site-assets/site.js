const samples = {
  mute: {
    label: 'Mute', x: '52%', y: '70%',
    ml: 'താഴത്തെ വരിയിൽ ഇടതുവശത്താണ് മ്യൂട്ട് ബട്ടൺ.',
    en: 'Mute is on the left of the bottom row.'
  },
  five: {
    label: '5', x: '31%', y: '27.5%',
    ml: 'നാല്, അഞ്ച്, ആറ് എന്ന വരിയിൽ നടുവിലാണ് അഞ്ച്.',
    en: 'Five is in the middle of the row with four, five, and six.'
  },
  power: {
    label: 'Power', x: '16.2%', y: '15.8%',
    ml: 'റിമോട്ടിന്റെ മുകൾഭാഗത്താണ് പവർ ബട്ടൺ. അക്കങ്ങളുള്ള ബട്ടണുകൾക്ക് മുകളിലാണ് അത്.',
    en: 'Power is at the top of the remote, above the number keys.'
  }
};

const player = document.querySelector('#voice');
const listen = document.querySelector('#listen');
const samplePlay = document.querySelector('#sample-play');
const finder = document.querySelector('.finder');
const status = document.querySelector('.audio-status');
const buttons = document.querySelectorAll('[data-key]');

function syncPlayback() {
  const playing = !player.paused && !player.ended;
  finder.classList.toggle('playing', playing);
  listen.classList.toggle('playing', playing);
  samplePlay.setAttribute('aria-label', playing ? 'Pause Malayalam example' : 'Play Malayalam example');
  document.querySelector('#listen-label').textContent = playing ? 'Pause demo' : 'Hear the demo';
}

async function playSample() {
  status.textContent = '';
  try {
    await player.play();
  } catch (error) {
    if (error.name !== 'AbortError') status.textContent = 'Audio unavailable. The transcript is shown above.';
  }
}

for (const button of buttons) {
  button.addEventListener('click', () => {
    const key = button.dataset.key;
    if (button.getAttribute('aria-pressed') === 'true') return;
    const sample = samples[key];
    const resume = !player.paused;
    for (const option of buttons) option.setAttribute('aria-pressed', String(option === button));
    player.pause();
    player.src = 'site-assets/' + key + '.mp3';
    finder.style.setProperty('--target-x', sample.x);
    finder.style.setProperty('--target-y', sample.y);
    document.querySelector('#marker').textContent = sample.label;
    document.querySelector('#line-ml').textContent = sample.ml;
    document.querySelector('#line-en').textContent = sample.en;
    status.textContent = '';
    if (resume) playSample();
  });
}

listen.hidden = false;
samplePlay.hidden = false;
player.controls = false;
samplePlay.addEventListener('click', () => player.paused ? playSample() : player.pause());
listen.addEventListener('click', () => player.paused ? playSample() : player.pause());
for (const event of ['playing', 'pause', 'ended', 'emptied']) player.addEventListener(event, syncPlayback);
player.addEventListener('error', () => { status.textContent = 'Audio unavailable. The transcript is shown above.'; });
