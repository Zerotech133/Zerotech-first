package com.qianjiangsoft.graphcore.config;

import cn.hutool.core.io.FileUtil;
import lombok.Data;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

/**
 * @author marshallwu
 * @date 2022/3/4 11:05
 */
@Component
@Data
public class ServerConfig {

    @Value("${qianjiang.file.spaceDataDir}")
    String spaceDataDir;

    @Value("${qianjiang.file.baseDir}")
    String baseDir;

    @Value("${qianjiang.engine.spacePersistenceEnable}")
    boolean spacePersistenceEnable;


    public String getSpaceDataDir() {
        if(!FileUtil.exist(spaceDataDir)) {
            FileUtil.mkdir(spaceDataDir);
        }
        return spaceDataDir;
    }
}
