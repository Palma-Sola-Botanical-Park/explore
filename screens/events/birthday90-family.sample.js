/* ============================================================================
   SAMPLE for the 90th birthday deck (screen-birthday90.html).
   Copy to birthday90-family.js in this folder and fill it in. That copy is
   gitignored — the repo is public — so put it on Office AND Galleria by hand
   (C:\PSBP\repo\screens\events\). Text only: photos live in R2.

   Photos: the PM number media intake gave it ("PM-000123"), or
   { src: "PM-000123", caption: "Cape Cod, 1978" }. Leave a slot out and its
   slide is skipped. Lists take up to 2 (childhood) or 3 (family, people).
============================================================================ */
window.BIRTHDAY90 = {
  name:  "Firstname Lastname",          // on the welcome slide
  short: "Firstname",                   // "Happy 90th, ___!" and "things we love about ___"
  born:  "1936-11-27",                  // YYYY-MM-DD; every "When you were N" is worked out from this
  bornLine: "Born in [town], [state].", // optional line on the 1936 slide

  photos: {
    welcome:   "PM-000000",             // joyful recent portrait
    favorite:  "PM-000000",             // a favorite picture of him
    baby:      "PM-000000",             // optional
    childhood: ["PM-000000", "PM-000000"],
    teen:      "PM-000000",
    young:     "PM-000000",
    family:    ["PM-000000", "PM-000000", "PM-000000"],
    then:      "PM-000000",
    now:       "PM-000000",
    life:      { src: "PM-000000", caption: "Travel, work, or a favorite hobby" },
    people:    ["PM-000000", "PM-000000", "PM-000000"],
    closing:   "PM-000000"
  },

  loves:   ["His laugh", "Sunday pancakes", "Never missing a game"],   // up to 4, one slide
  sayings: ["One of his sayings the whole family can finish"],         // one slide each

  // Leave out any slide by its id, e.g. music he wouldn't have liked:
  // welcome favorite year magazine classmates prices childhood notyet robinson
  // teen lucy unforgettable young fifties family sixties thennow beatles life
  // moon numbers people loves toast closing
  skip: []
};
