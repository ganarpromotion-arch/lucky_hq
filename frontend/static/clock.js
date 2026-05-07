/* Lucky HQ — 한국시간 시계 (모든 페이지 공유)
   #clock 엘리먼트 있으면 1초마다 KST로 갱신.
   사용자 브라우저 타임존에 의존하지 않고 항상 Asia/Seoul로 표시.
*/
(function () {
  const el = document.getElementById('clock');
  if (!el) return;

  const fmt = new Intl.DateTimeFormat('ko-KR', {
    timeZone: 'Asia/Seoul',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  });

  function tick() {
    const parts = fmt.formatToParts(new Date());
    const get = (t) => parts.find(p => p.type === t)?.value || '';
    const date = `${get('month')}.${get('day')}`;
    const time = `${get('hour')}:${get('minute')}:${get('second')}`;
    el.textContent = `${date}  ${time} KST`;
  }

  tick();
  setInterval(tick, 1000);
})();
