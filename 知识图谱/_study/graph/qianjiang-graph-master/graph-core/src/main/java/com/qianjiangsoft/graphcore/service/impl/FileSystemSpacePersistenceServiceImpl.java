package com.qianjiangsoft.graphcore.service.impl;

import cn.hutool.core.bean.BeanUtil;
import cn.hutool.core.collection.CollectionUtil;
import cn.hutool.core.date.DateUtil;
import cn.hutool.core.io.FileUtil;
import com.qianjiangsoft.graphcore.config.ServerConfig;
import com.qianjiangsoft.graphcore.engine.GraphEngine;
import com.qianjiangsoft.graphcore.domain.entity.GraphSpace;
import com.qianjiangsoft.graphcore.mapper.GraphSpaceMapper;
import com.qianjiangsoft.graphcore.model.Space;
import com.qianjiangsoft.graphcore.model.SpaceData;
import com.qianjiangsoft.graphcore.service.SpacePersistenceService;
import com.qianjiangsoft.graphcore.service.SpaceSyncService;
import com.qianjiangsoft.graphcore.util.KryoUtil;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;

import java.io.File;

/**
 * @author marshallwu
 * @date 2022/3/4 10:10
 */
@Service
@Slf4j
public class FileSystemSpacePersistenceServiceImpl extends SpacePersistenceService {

    @Autowired
    ServerConfig serverConfig;

    @Autowired
    GraphEngine graphEngine;

    @Autowired
    GraphSpaceMapper graphSpaceMapper;

    @Autowired
    SpaceSyncService spaceSyncService;

    static final String SPACE_FILE_EXT = ".data";

    private String getSpaceFilePath(String spaceId) {
        return serverConfig.getSpaceDataDir() + File.separator + spaceId + SPACE_FILE_EXT;
    }

    @Override
    public void deleteSpaceFile(String spaceId) {
        String filePath = getSpaceFilePath(spaceId);

        //清除文件
        FileUtil.del(filePath);
    }

    @Override
    public void saveSpace(Space space) {
        log.info("开始保存space[{}]数据", space.getId());
        String filePath = getSpaceFilePath(space.getId());

        //填充数据
        SpaceData spaceData = BeanUtil.copyProperties(space, SpaceData.class);

        //清除文件
        FileUtil.del(filePath);

        //保存数据
        if(CollectionUtil.isNotEmpty(spaceData.getEdgeList()) &&
                CollectionUtil.isNotEmpty(spaceData.getVertexList())) {

            try {
                KryoUtil.writeObjectToFile(filePath, spaceData);
            }catch (Exception e) {
                e.printStackTrace();
                log.error("空间[{}]数据文件写入出错", space.getId());
            }

            //更新保存时间
            GraphSpace graphSpace = graphSpaceMapper.selectById(space.getId());
            graphSpace.setLastSavedTime(DateUtil.date());
            graphSpaceMapper.updateById(graphSpace);

            log.info("结束保存space[{}]数据", space.getId());
        }
    }

    @Override
    public SpaceData readSpaceData(GraphSpace graphSpace) {

        //文件名
        String fileName = graphSpace.getId() + SPACE_FILE_EXT;

        String filePath = serverConfig.getSpaceDataDir() + File.separator + fileName;

        log.info("开始读取space[{}]文件", filePath);

        if(!FileUtil.exist(filePath)) {
            return new SpaceData();
        }

        //读取文件
        SpaceData spaceData = null;

        try {
            spaceData = KryoUtil.readObjectFromFile(filePath, SpaceData.class);
            log.info("成功读取space[{}]文件", filePath);
        }catch (Exception e) {
            e.printStackTrace();
            log.error("读取space[{}]文件失败", filePath, e);
        }
        return spaceData;
    }

}
