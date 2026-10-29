// 해시 라우터. 정적 서빙에서도 동작한다.
//   #/                     메인(작품 목록)
//   #/novels/{uid}/ideas    아이디어/구조
//   #/novels/{uid}/design   세부 설계
//   #/novels/{uid}/monitor  집필 모니터
//   #/novels/{uid}/review   작가 검토
//   #/novels/{uid}/entities 엔티티 브라우저
export const SCREENS = {
  ideas: "ideas",
  design: "sdesign",
  monitor: "s2",
  review: "s3",
  entities: "s4",
};

export function parseHash(hash) {
  const h = (hash ?? window.location.hash).replace(/^#/, "") || "/";
  const m = h.match(/^\/novels\/([^/]+)\/([^/]+)\/?$/);
  if (m && SCREENS[m[2]]) return { name: "studio", uid: m[1], screen: m[2] };
  return { name: "home" };
}

export function current() {
  return parseHash();
}

export function go(path) {
  const target = `#${path}`;
  if (window.location.hash === target) {
    window.dispatchEvent(new HashChangeEvent("hashchange"));
  } else {
    window.location.hash = target;
  }
}

export function novelPath(uid, screen) {
  return `/novels/${uid}/${screen}`;
}

export function onChange(fn) {
  window.addEventListener("hashchange", () => fn(current()));
}
