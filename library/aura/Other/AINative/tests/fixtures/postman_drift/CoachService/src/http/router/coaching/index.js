/**
 * @module router
 * @description Express router for /coaching
 * @copyright Aura Health, Inc.
 */

// External libraries
const express = require('express');

// Internal dependencies
const {ready, status} = require('../../handlers');
const automatedMessagesRouter = require('./automatedMessages');
const homeworksRouter = require('./homeworks');
const subscriptionsRouter = require('./subscriptions');
const cannedResponsesRouter = require('./cannedResponses');
const packageTemplatesRouter = require('./packageTemplates');
const coachPackagesRouter = require('./coachPackages');
const notesRouter = require('./notes');

const router = express.Router();

// Routes for the service status
router.get('/ready', ready);
router.get('/status', status);

router.use('/automatedMessages', automatedMessagesRouter);
router.use('/homeworks', homeworksRouter);
router.use('/subscriptions', subscriptionsRouter);
router.use('/notes', notesRouter);
router.use('/cannedResponses', cannedResponsesRouter);
router.use('/packageTemplates', packageTemplatesRouter);
router.use('/coachPackages', coachPackagesRouter);

module.exports = router;
