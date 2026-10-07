package com.qianjiangsoft.graphcore.engine;

import com.qianjiangsoft.graphcore.config.ServerConfig;
import com.qianjiangsoft.graphcore.model.Space;
import com.qianjiangsoft.graphcore.service.SpacePersistenceService;
import com.qianjiangsoft.graphcore.service.SpaceService;
import org.springframework.beans.factory.DisposableBean;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Component;

import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

/**
 * @author marshallwu
 * @date 2022/2/24 9:28
 */
@Component
public class SimpleGraphEngine implements GraphEngine, DisposableBean {

    Map<String, Space> spaceMap = new ConcurrentHashMap<>();

    @Autowired
    SpacePersistenceService spacePersistenceService;

    @Autowired
    SpaceService spaceService;

    @Autowired
    ServerConfig serverConfig;

    @Override
    public void initEngine() {
        if(serverConfig.isSpacePersistenceEnable()) {
            spacePersistenceService.recoverData();
        }
    }

    @Override
    public void startEngine() {

    }

    @Override
    public Map<String, Space> getSpaceMap() {
        return spaceMap;
    }

    @Override
    public void destroy() {
//        if(serverConfig.isSpacePersistenceEnable()) {
//            spacePersistenceService.saveData();
//        }
    }
}
