-- Build docref records from the installed manuals' numbered PDF bookmarks.
-- Some of these headings have no source label. Read the actual destinations
-- instead of assuming that section numbers and PDF anchor names coincide.
local manuals = {
  {package = "babel", headings = {
    fonts = "Fonts in Unicode engines",
    selectors = "Basic language selectors",
    ["aux-selectors"] = "Auxiliary language selectors",
    options = "Package options",
    provide = "Creating a language",
    dates = "Dates",
  }},
  {package = "babelbidi", headings = {
    loading = "Loading",
    fallback = "Font fallback",
    commands = "Commands",
    environments = "Environments",
    languages = "Language Selection",
    patches = "Patches",
    minted = "Minted",
  }},
}

local function text(s)
  if s:sub(1, 2) ~= "\254\255" then return s end
  local out = {}
  for i = 3, #s - 1, 2 do
    out[#out + 1] = utf8.char(s:byte(i) * 256 + s:byte(i + 1))
  end
  return table.concat(out)
end

-- Resolve named destinations to printed page labels, including front matter.
local function page_labels(pdf)
  local catalog = pdfe.getcatalog(pdf)
  local pages, rules, destinations = {}, {}, {}
  for n, page in ipairs(pdfe.pagestotable(pdf)) do pages[page[3]] = n end
  local function number_tree(tree)
    if not tree then return end
    local nums = pdfe.getarray(tree, "Nums")
    if nums then
      for i = 0, #nums - 1, 2 do
        local rule = pdfe.getdictionary(nums, i + 1)
        rules[#rules + 1] = {first = pdfe.getinteger(nums, i),
          style = pdfe.getname(rule, "S"),
          prefix = text(pdfe.getstring(rule, "P", true) or ""),
          start = pdfe.getinteger(rule, "St") or 1}
      end
    end
    local kids = pdfe.getarray(tree, "Kids")
    if kids then
      for i = 0, #kids - 1 do number_tree(pdfe.getdictionary(kids, i)) end
    end
  end
  number_tree(pdfe.getdictionary(catalog, "PageLabels"))
  table.sort(rules, function(a, b) return a.first < b.first end)
  local function label(page)
    local rule
    for _, item in ipairs(rules) do
      if item.first > page - 1 then break end
      rule = item
    end
    if not rule then return tostring(page) end
    local n, value = page - 1 - rule.first + rule.start, ""
    if rule.style == "D" then value = tostring(n)
    elseif rule.style == "r" or rule.style == "R" then
      for _, pair in ipairs({{1000,"m"},{900,"cm"},{500,"d"},{400,"cd"},
        {100,"c"},{90,"xc"},{50,"l"},{40,"xl"},{10,"x"},
        {9,"ix"},{5,"v"},{4,"iv"},{1,"i"}}) do
        while n >= pair[1] do value, n = value .. pair[2], n - pair[1] end
      end
      if rule.style == "R" then value = value:upper() end
    elseif rule.style == "a" or rule.style == "A" then
      value = string.rep(string.char((n - 1) % 26 + 97), math.floor((n - 1) / 26) + 1)
      if rule.style == "A" then value = value:upper() end
    end
    return rule.prefix .. value
  end
  local function read_destination(container, key)
    local array = pdfe.getarray(container, key)
    if not array then
      local dict = pdfe.getdictionary(container, key)
      array = dict and pdfe.getarray(dict, "D")
    end
    if not array then return end
    local kind, _, object = pdfe.getfromarray(array, 1)
    return kind == 10 and pages[object] and label(pages[object])
  end
  local function name_tree(tree)
    if not tree then return end
    local names = pdfe.getarray(tree, "Names")
    if names then
      for i = 0, #names - 1, 2 do
        destinations[pdfe.getstring(names, i, true)] = read_destination(names, i + 1)
      end
    end
    local kids = pdfe.getarray(tree, "Kids")
    if kids then
      for i = 0, #kids - 1 do name_tree(pdfe.getdictionary(kids, i)) end
    end
  end
  local names = pdfe.getdictionary(catalog, "Names")
  if names then name_tree(pdfe.getdictionary(names, "Dests")) end
  return destinations
end

for _, manual in ipairs(manuals) do
  local name = manual.package .. ".pdf"
  local path = assert(kpse.find_file(name, "TeX system documentation")
    or kpse.find_file(name), "Cannot find " .. name)
  local pdf = assert(pdfe.open(path), "Cannot open " .. path)
  local pages = page_labels(pdf)
  local records = {}
  local function walk(item)
    while item do
      local title = text(assert(pdfe.getstring(item, "Title", true)))
      local number, heading = title:match("^(%d[%d.]*)%s+(.+)$")
      for label, wanted in pairs(manual.headings) do
        if heading == wanted then
          local action = pdfe.getdictionary(item, "A")
          local destination = pdfe.getstring(item, "Dest", true)
            or (action and pdfe.getname(action, "S") == "GoTo"
              and pdfe.getstring(action, "D", true))
          assert(destination, "No named destination for " .. wanted)
          assert(not records[label], "Ambiguous heading: " .. wanted)
          local page = assert(pages[destination], "No page for " .. wanted)
          records[label] = string.format("\\newlabel{%s}{{%s}{%s}{%s}{%s}{}}\n",
            label, number, page, heading, destination)
        end
      end
      walk(pdfe.getdictionary(item, "First"))
      item = pdfe.getdictionary(item, "Next")
    end
  end
  local outline = assert(pdfe.getdictionary(pdfe.getcatalog(pdf), "Outlines"))
  walk(pdfe.getdictionary(outline, "First"))
  pdfe.close(pdf)
  local labels = {}
  for label in pairs(manual.headings) do
    assert(records[label], "Missing heading: " .. manual.headings[label])
    labels[#labels + 1] = label
  end
  table.sort(labels)
  local auxiliary = manual.package .. "-reference.aux"
  local output = assert(io.open(auxiliary, "w"))
  output:write("% Generated from the installed " .. name .. " bookmarks.\n")
  for _, label in ipairs(labels) do output:write(records[label]) end
  output:close()
  -- Preserve the resolved PDF path, including manuals in the local TeX tree.
  tex.sprint("\\DocRefSource{" .. manual.package .. "}{" .. auxiliary
    .. "}[" .. path .. "]")
end
