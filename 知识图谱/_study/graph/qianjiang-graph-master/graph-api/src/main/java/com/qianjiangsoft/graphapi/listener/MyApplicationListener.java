package com.qianjiangsoft.graphapi.listener;

import com.qianjiangsoft.graphcore.engine.GraphEngine;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.context.event.ApplicationReadyEvent;
import org.springframework.context.ApplicationListener;
import org.springframework.stereotype.Component;

@Component
public class MyApplicationListener implements ApplicationListener<ApplicationReadyEvent> {

    @Autowired
    GraphEngine graphEngine;

    @Override
    public void onApplicationEvent(ApplicationReadyEvent event) {
        graphEngine.initEngine();
        graphEngine.startEngine();
    }
}