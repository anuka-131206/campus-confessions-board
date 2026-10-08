INSERT INTO posts (id, body, status, score, reports, created_at) VALUES
 (1, 'The library third floor is the only place on campus with working plug sockets. Please stop telling people.', 'published', 42, 0, now() - interval '6 hours'),
 (2, 'I have been pretending to understand pointers since first year. It is now third year.', 'published', 128, 1, now() - interval '2 days'),
 (3, 'Whoever keeps microwaving fish in the hostel common room: I know it is you.', 'published', 7, 2, now() - interval '3 hours'),
 (4, 'Confession: I joined the robotics club entirely for the free pizza and I have no regrets.', 'published', 19, 0, now() - interval '1 day'),
 (5, 'The canteen chai has been the same temperature since 2019 and I think it is a single continuous cup.', 'published', 63, 0, now() - interval '9 hours'),
 (6, 'whoever took my charger from lab 4 is a stup1d l0ser and I hope your laptop dies', 'held', 0, 0, now() - interval '20 minutes'),
 (7, 'the new timetable is tr4sh and whoever wrote it is a m.o.r.o.n', 'held', 0, 0, now() - interval '45 minutes'),
 (8, 'I cried in the stairwell after my viva and then went back in and passed. It gets better.', 'published', 211, 0, now() - interval '4 days'),
 (9, 'Someone has been slowly stealing one chair a week from the seminar room. There are four left.', 'published', 2, 2, now() - interval '30 minutes')
ON CONFLICT (id) DO NOTHING;
SELECT setval('posts_id_seq', GREATEST((SELECT MAX(id) FROM posts), 1));

INSERT INTO comments (id, post_id, body, created_at) VALUES
 (1, 1, 'Too late, I already told my entire batch.', now() - interval '5 hours'),
 (2, 1, 'There are two more behind the periodicals shelf.', now() - interval '4 hours'),
 (3, 2, 'Same, and I am teaching a tutorial on them next week.', now() - interval '1 day'),
 (4, 2, 'This is the most relatable thing on this board.', now() - interval '20 hours'),
 (5, 8, 'Needed to read this today. Thank you.', now() - interval '3 days'),
 (6, 5, 'It is a sourdough starter at this point.', now() - interval '8 hours')
ON CONFLICT (id) DO NOTHING;
SELECT setval('comments_id_seq', GREATEST((SELECT MAX(id) FROM comments), 1));

INSERT INTO moderation_log (id, post_id, action, reason, created_at) VALUES
 (1, 6, 'held', 'word filter: loser, stupid', now() - interval '20 minutes'),
 (2, 7, 'held', 'word filter: moron, trash', now() - interval '45 minutes')
ON CONFLICT (id) DO NOTHING;
SELECT setval('moderation_log_id_seq', GREATEST((SELECT MAX(id) FROM moderation_log), 1));
