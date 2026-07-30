import { createAvatar } from '@prometheusavatar/core';

const $c = document.getElementById('avatar-container');
const $s = document.getElementById('status');
function info(t,cls){$s.textContent=t;$s.className=cls||'';}

info('加载 Haru...');
try {
  const avatar = await createAvatar({
    container: $c,
    modelUrl: '/live2d-resources/Haru/Haru.model3.json',
  });
  avatar.on('model:loaded',()=>{info('Haru ✅','ok');setTimeout(()=>{avatar.speak('Hello!');info('🎤','ok')},3000)});
  avatar.on('model:error',e=>info(e+'','err'));
  window.avatar = avatar;
} catch(e) { info(e.message,'err'); console.error(e); }
