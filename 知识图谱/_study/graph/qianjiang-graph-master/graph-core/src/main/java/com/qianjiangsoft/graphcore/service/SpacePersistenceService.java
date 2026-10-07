package com.qianjiangsoft.graphcore.service;

import cn.hutool.core.bean.BeanUtil;
import cn.hutool.core.collection.CollectionUtil;
import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.qianjiangsoft.graphcore.engine.GraphEngine;
import com.qianjiangsoft.graphcore.domain.entity.GraphSpace;
import com.qianjiangsoft.graphcore.mapper.GraphSpaceMapper;
import com.qianjiangsoft.graphcore.model.Space;
import com.qianjiangsoft.graphcore.model.SpaceData;
import com.qianjiangsoft.graphcore.model.Vertex;
import lombok.SneakyThrows;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.scheduling.concurrent.ThreadPoolTaskExecutor;

import java.util.List;
import java.util.concurrent.CountDownLatch;
import java.util.stream.Collectors;

/**
 * @author marshallwu
 * @date 2022/3/4 10:09
 */
@Slf4j
public abstract class SpacePersistenceService {

    @Autowired
    GraphEngine graphEngine;

    @Autowired
    GraphSpaceMapper graphSpaceMapper;

    @Autowired
    SpaceSyncService spaceSyncService;

    @Autowired
    private ThreadPoolTaskExecutor commonThreadPoolTaskExecutor;

    public abstract void saveSpace(Space space);

    public abstract SpaceData readSpaceData(GraphSpace graphSpace);

    public abstract void deleteSpaceFile(String spaceId);

    @SneakyThrows
    public void saveData() {

        LambdaQueryWrapper<GraphSpace> queryWrapper = new LambdaQueryWrapper<>();
        queryWrapper.eq(GraphSpace::getActiveFlag, 1);
        List<GraphSpace> graphSpaceList = graphSpaceMapper.selectList(queryWrapper);

        CountDownLatch countDownLatch = new CountDownLatch(graphSpaceList.size());

        for (GraphSpace graphSpace : graphSpaceList) {

            //取出space
            Space space = graphEngine.getSpaceMap().get(graphSpace.getId());

            if(space == null) {
                countDownLatch.countDown();
                continue;
            }

            //保存数据
            commonThreadPoolTaskExecutor.execute(()->{

                //判断时间是否早于上次更新时间
                if(graphSpace.getLastSavedTime() == null ||
                        graphSpace.getLastSavedTime().before(graphSpace.getUpdateTime())) {
                    this.saveSpace(space);
                }

                countDownLatch.countDown();
            });
        }

        countDownLatch.await();
    }

    @SneakyThrows
    public void recoverData(){
        log.info("开始恢复所有space数据");
        LambdaQueryWrapper<GraphSpace> queryWrapper = new LambdaQueryWrapper<>();
        queryWrapper.eq(GraphSpace::getActiveFlag, 1);
        List<GraphSpace> spaceList = graphSpaceMapper.selectList(queryWrapper);

        CountDownLatch countDownLatch = new CountDownLatch(spaceList.size());
        for (GraphSpace graphSpace : spaceList) {
            commonThreadPoolTaskExecutor.execute(() -> {
                try {
                    this.loadSpace(graphSpace);
                }catch (Exception e) {

                }finally {
                    countDownLatch.countDown();
                }
            });
        }
        countDownLatch.await();
        log.info("结束恢复所有space数据");
    }

    public void loadSpace(GraphSpace graphSpace){
        log.info("恢复space[{}]数据开始", graphSpace.getId());

        Space space = new Space(graphSpace.getId(), graphSpace.getName());

        //放入space
        graphEngine.getSpaceMap().put(space.getId(), space);

        try {
            SpaceData spaceData = readSpaceData(graphSpace);

            if(spaceData == null) {
                log.info("恢复space[{}]数据失败", graphSpace.getId());
                return;
            }

            //恢复space
            if(CollectionUtil.isNotEmpty(spaceData.getVertexList()) && CollectionUtil.isNotEmpty(spaceData.getEdgeList())) {

                //复制内容
                BeanUtil.copyProperties(spaceData, space);

                //转换vertex map
                space.setVertexMap(space.getVertexList().stream().collect(Collectors.toMap(Vertex::getId, vertex -> vertex)));

                //更新同步信息
                spaceSyncService.syncSpaceInfo(space.getId());
            }

            log.info("恢复space[{}]数据完成", graphSpace.getId());

        }catch (Exception e) {
            e.printStackTrace();
            log.error("恢复space[{}]数据失败", graphSpace.getId(), e);
        }

    }
}
