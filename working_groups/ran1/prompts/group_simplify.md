You are sorting the sessions of a 3GPP RAN1 meeting schedule into legend categories.

## What you are working with

The schedule is drawn as a Gantt chart. Each session carries a group label, and the chart
gives every distinct label its own color and its own legend entry. A participant uses the
colors to see at a glance which top-level work area a session belongs to, and uses the
legend to filter the chart down to one area.

The labels come from schedule cells typed by hand by the chair and the vice-chairs, each
in their own file. That makes the raw labels inconsistent in two ways:

- They carry detail the legend should not have: a sub-topic or feature name, an agenda
  item number, or a path of nested headers joined by " / ".
- The same work area is written differently by different people, or by the same person in
  different cells: a shorter and a longer form of one name, an abbreviation with or
  without a trailing letter, "Rel-N" next to "RN", different capitalization or
  punctuation.

You receive every distinct label in the schedule together with the number of sessions
that use it. Map each label to the legend category it belongs to.

## What a good set of categories looks like

A category is one top-level work area of the meeting: usually the work of one release or
one study programme, plus areas that stand beside the releases, such as maintenance of
earlier releases. A RAN1 meeting has only a handful of these, so the finished legend is
short. Topics, features and agenda sub-clauses inside an area are not categories of their
own; they take the category of the area they belong to.

Each work area gets exactly one category. This matters more than anything else here. Two
legend entries that a participant would read as the same work area are a defect: the
area's sessions come out in two colors, and filtering by one entry hides the rest.

So before you settle on the category names, compare them with one another and ask whether
a participant would call any two of them the same area. When one name is a shorter, longer
or looser form of another, treat them as one area, unless the labels themselves show the
two names being used for clearly different work.

## Choosing the name

Name each category with a spelling that already appears in the input, reduced to the bare
area name: no path, no topic, no agenda number.

When an area appears under several spellings, use the one that the most sessions use. Add
up the session counts of every label that starts with a spelling to compare them. The
legend then reads the way the chairs most often write it.

## Special cases

- A label that names two areas goes to the one named first, unless the rest of the list
  makes it clear that its sessions belong to the other.
- A label that is only an agenda item reference takes the name of its work area when
  another label in the list shows which area that is. Otherwise keep the reference itself
  as the category.
- A placeholder that names no work area, such as time still to be assigned by someone,
  maps to "TBD".

## Output

Return only JSON in this form:

{"mappings": [{"original": "<input label>", "simplified": "<category>"}]}

Every input label appears exactly once as "original", copied character for character,
because the mapping is applied by exact string match.
