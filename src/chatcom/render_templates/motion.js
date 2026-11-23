/* CHATCOM episode motion: Web Animations only (no animation library, no network).
   Every time is in seconds from the start of the episode, written by the composer
   as data attributes; HyperFrames seeks document.getAnimations() frame by frame.
     data-show="a,b;c,d"  visible only inside each window (pose pictures, stills)
     data-pop="a,b"       pops in at a, fades out at b (bubbles, stickers)
     data-rise="a,b"      fades in rising at a, fades out at b (cards, captions)
     data-slide="a,b"     slides in from the right at a, out to the left at b (credit cards)
     data-stamp="a,b"     stamps down at a (renewal)
     data-drift="a,b"     slow push-in across the window (backgrounds)
     data-lift="a,b;c,d"  a speaking figure lifts a little inside each window */
(function () {
  var MS = 1000, POP = 0.24, OUT = 0.15;
  function windows(v) {
    return v.split(";").filter(Boolean).map(function (w) {
      var p = w.split(",");
      return [parseFloat(p[0]), parseFloat(p[1])];
    });
  }
  function play(el, frames, a, b, easing) {
    var dur = Math.max(0.001, b - a);
    var anim = el.animate(frames(dur), { delay: a * MS, duration: dur * MS, fill: "none", easing: easing || "linear" });
    anim.pause();
  }
  function edges(dur, inn, out) {
    return [Math.min(inn / dur, 0.45), Math.max(1 - out / dur, 0.55)];
  }
  var kinds = {
    show: function (dur) { return [{ opacity: 1 }, { opacity: 1 }]; },
    pop: function (dur) {
      var e = edges(dur, POP, OUT);
      return [{ opacity: 0, transform: "translateY(30px) scale(.6)", offset: 0, easing: "cubic-bezier(.34,1.56,.64,1)" },
              { opacity: 1, transform: "none", offset: e[0] },
              { opacity: 1, transform: "none", offset: e[1] },
              { opacity: 0, transform: "none", offset: 1 }];
    },
    rise: function (dur) {
      var e = edges(dur, 0.35, 0.2);
      return [{ opacity: 0, transform: "translateY(24px)", offset: 0, easing: "cubic-bezier(.2,0,0,1)" },
              { opacity: 1, transform: "none", offset: e[0] },
              { opacity: 1, transform: "none", offset: e[1] },
              { opacity: 0, transform: "none", offset: 1 }];
    },
    slide: function (dur) {
      var e = edges(dur, 0.4, 0.3);
      return [{ opacity: 0, transform: "translateX(60px)", offset: 0, easing: "cubic-bezier(.2,0,0,1)" },
              { opacity: 1, transform: "none", offset: e[0] },
              { opacity: 1, transform: "none", offset: e[1], easing: "ease-in" },
              { opacity: 0, transform: "translateX(-60px)", offset: 1 }];
    },
    stamp: function (dur) {
      var e = edges(dur, 0.25, 0.01);
      return [{ opacity: 0, transform: "rotate(-6deg) scale(2.2)", offset: 0, easing: "cubic-bezier(.7,0,1,1)" },
              { opacity: 1, transform: "rotate(-6deg) scale(1)", offset: e[0] },
              { opacity: 1, transform: "rotate(-6deg) scale(1)", offset: 1 }];
    },
    drift: function (dur) {
      return [{ transform: "scale(1.04)" }, { transform: "scale(1.12)" }];
    },
    lift: function (dur) {
      var e = edges(dur, 0.18, 0.18);
      return [{ transform: "none", offset: 0, easing: "ease-out" },
              { transform: "translateY(-14px)", offset: e[0] },
              { transform: "translateY(-14px)", offset: e[1], easing: "ease-in" },
              { transform: "none", offset: 1 }];
    }
  };
  Object.keys(kinds).forEach(function (kind) {
    var attr = "data-" + kind;
    document.querySelectorAll("[" + attr + "]").forEach(function (el) {
      windows(el.getAttribute(attr)).forEach(function (w) { play(el, kinds[kind], w[0], w[1]); });
    });
  });
})();
