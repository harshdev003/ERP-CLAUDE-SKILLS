/**
 * @module router
 * @description Express router for /
 * @copyright Aura Health, Inc.
 */

// External libraries
const express = require('express');

// Internal dependencies
const {ready, status} = require('../handlers');
const chatRouter = require('./chat');
const coachingRouter = require('./coaching');
const communitiesRouter = require('./communities');
const eventsRouter = require('./events');
const forumsRouter = require('./forums');
const moderateRouter = require('./moderate');
const schedulingRouter = require('./scheduling');
const videoConferencesRouter = require('./videoConferences');

const router = express.Router();

// Routes for the Kubernetes probes (alive / ready)
router.get('/', (req, res) => res.sendStatus(200));
router.get('/ready', ready);
router.get('/status', status);

router.use('/chat', chatRouter);
router.use('/coaching', coachingRouter);
router.use('/communities', communitiesRouter);
router.use('/events', eventsRouter);
router.use('/forums', forumsRouter);
router.use('/moderate', moderateRouter);
router.use('/scheduling', schedulingRouter);
router.use('/videoConferences', videoConferencesRouter);

module.exports = router;
